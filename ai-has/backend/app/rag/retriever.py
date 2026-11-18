"""Hybrid RAG retriever combining ChromaDB dense vector search and BM25 lexical search."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from app.rag.ingest import KnowledgeBaseManager, get_kb_manager, tokenize_for_bm25

logger = logging.getLogger(__name__)

_CODE_PATTERN = re.compile(r"\b(?:vpn-\d+|sso-\d+|sec-lock|sop-it-\d+|mfa-err-[a-z]+|550\s*5\.7\.1)\b", re.IGNORECASE)


@dataclass
class CitationResult:
    """Retrieved SOP chunk formatted with hybrid confidence and citation metadata."""

    doc_id: str
    filename: str
    doc_code: str
    title: str
    section: str
    chunk_id: str
    score: float
    vector_score: float
    bm25_score: float
    excerpt: str

    def to_dict(self) -> Dict:
        return asdict(self)


class HybridRetriever:
    """Reciprocal Rank Fusion (RRF) over ChromaDB cosine vector search and BM25Okapi."""

    def __init__(self, kb_manager: Optional[KnowledgeBaseManager] = None) -> None:
        self.kb_manager = kb_manager or get_kb_manager()

    def retrieve(self, query: str, top_k: int = 5) -> List[CitationResult]:
        if not query or not query.strip():
            return []

        if not self.kb_manager.chunks:
            self.kb_manager._refresh_bm25_from_chroma()

        chunks = self.kb_manager.chunks
        if not chunks:
            return []

        n_candidates = min(len(chunks), max(top_k * 3, 12))

        # 1. Dense Vector Search via ChromaDB (BM25-only if the embedding provider is down)
        try:
            collection = self.kb_manager._get_collection()
            query_embedding = self.kb_manager.embedder.embed_query(query)
            vec_res = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_candidates,
                include=["distances"],
            )
            vec_ids = (vec_res.get("ids") or [[]])[0]
            vec_dists = (vec_res.get("distances") or [[]])[0]
        except Exception:
            logger.exception("Vector search failed; using BM25 lexical results only")
            vec_ids, vec_dists = [], []

        vec_ranks: Dict[str, int] = {}
        vec_scores: Dict[str, float] = {}
        for rank, (cid, dist) in enumerate(zip(vec_ids, vec_dists), start=1):
            vec_ranks[cid] = rank
            vec_scores[cid] = max(0.0, min(1.0, 1.0 - float(dist)))

        # 2. Sparse Lexical Search via BM25Okapi
        bm25_ranks: Dict[str, int] = {}
        bm25_norm_scores: Dict[str, float] = {}
        if self.kb_manager.bm25 is not None:
            q_tokens = tokenize_for_bm25(query)
            if q_tokens:
                raw_scores = self.kb_manager.bm25.get_scores(q_tokens)
                scored_pairs = sorted(
                    enumerate(raw_scores),
                    key=lambda item: float(item[1]),
                    reverse=True,
                )[:n_candidates]
                for rank, (idx, raw_score) in enumerate(scored_pairs, start=1):
                    val = float(raw_score)
                    if val > 0:
                        chunk = chunks[idx]
                        bm25_ranks[chunk.chunk_id] = rank
                        # Saturating normalization prevents weak single-word hits from scoring 1.0
                        bm25_norm_scores[chunk.chunk_id] = val / (val + 6.0)

        # 3. Reciprocal Rank Fusion + Exact Error-Code Boost
        rrf_k = 60.0
        max_possible_rrf = (0.55 / (rrf_k + 1.0)) + (0.45 / (rrf_k + 1.0))
        query_codes = [c.lower() for c in _CODE_PATTERN.findall(query)]
        query_tokens = set(tokenize_for_bm25(query))

        candidate_ids = set(vec_ranks.keys()) | set(bm25_ranks.keys())
        ranked: List[CitationResult] = []

        for cid in candidate_ids:
            chunk = self.kb_manager.chunk_by_id.get(cid)
            if not chunk:
                continue

            rrf_val = 0.0
            if cid in vec_ranks:
                rrf_val += 0.55 / (rrf_k + vec_ranks[cid])
            if cid in bm25_ranks:
                rrf_val += 0.45 / (rrf_k + bm25_ranks[cid])

            rrf_norm = rrf_val / max_possible_rrf
            v_score = vec_scores.get(cid, 0.0)
            b_score = bm25_norm_scores.get(cid, 0.0)

            # Lexical overlap sanity signal so out-of-domain queries don't inflate score
            overlap_ratio = (
                len(query_tokens & set(chunk.tokens)) / max(len(query_tokens), 1)
                if query_tokens
                else 0.0
            )
            code_boost = 0.0
            if query_codes:
                chunk_lower = chunk.text.lower()
                if any(code in chunk_lower for code in query_codes):
                    code_boost = 0.15

            combined_score = min(
                1.0,
                0.45 * rrf_norm + 0.25 * b_score + 0.18 * v_score + 0.12 * overlap_ratio + code_boost,
            )

            # Penalize candidates that lack meaningful lexical or vector alignment (out-of-scope guard)
            if (b_score < 0.20 and overlap_ratio < 0.15 and v_score < 0.30) or (
                overlap_ratio < 0.20 and v_score < 0.12
            ):
                combined_score *= 0.25

            excerpt = chunk.text[:420].strip()
            if len(chunk.text) > 420:
                excerpt += "..."

            ranked.append(
                CitationResult(
                    doc_id=chunk.doc_id,
                    filename=chunk.filename,
                    doc_code=chunk.doc_code,
                    title=chunk.title,
                    section=chunk.section,
                    chunk_id=chunk.chunk_id,
                    score=round(combined_score, 4),
                    vector_score=round(v_score, 4),
                    bm25_score=round(b_score, 4),
                    excerpt=excerpt,
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:top_k]

    def format_context(self, citations: List[CitationResult]) -> str:
        """Render retrieved chunks into numbered grounded SOP context blocks for the LLM."""
        if not citations:
            return "NO MATCHING IT SOP DOCUMENTS FOUND."

        blocks: List[str] = []
        for idx, c in enumerate(citations, start=1):
            chunk = self.kb_manager.chunk_by_id.get(c.chunk_id)
            full_text = chunk.text if chunk else c.excerpt
            blocks.append(
                f"[{idx}] Source: {c.doc_code} ({c.filename}) | Section: {c.section} | Confidence: {c.score:.2f}\n"
                f"{full_text}"
            )
        return "\n\n---\n\n".join(blocks)
