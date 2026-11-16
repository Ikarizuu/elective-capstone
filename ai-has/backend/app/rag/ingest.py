"""Document ingestion, chunking, embedding, and ChromaDB + BM25 indexing."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from openai import OpenAI
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import KBDocument, utcnow

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_DOC_ID_RE = re.compile(r"\*\*Document ID:\*\*\s*`([^`]+)`", re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_STOPWORDS = {
    "a", "about", "after", "an", "and", "are", "as", "at", "be", "before",
    "by", "can", "could", "do", "does", "for", "from", "give", "have", "how",
    "i", "if", "in", "into", "is", "it", "me", "more", "most", "my", "next",
    "of", "on", "or", "over", "please", "should", "that", "the", "their",
    "them", "there", "these", "this", "to", "under", "was", "we", "what",
    "when", "where", "which", "who", "why", "will", "with", "would", "you",
    "your",
}


def tokenize_for_bm25(text: str) -> List[str]:
    """Tokenize text while preserving hyphen/underscore IT codes and filtering stopwords."""
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    expanded: List[str] = []
    for tok in tokens:
        if tok in _STOPWORDS:
            continue
        expanded.append(tok)
        if "-" in tok or "_" in tok:
            for part in re.split(r"[-_]", tok):
                if part and part not in _STOPWORDS:
                    expanded.append(part)
    return expanded


@dataclass
class IndexedChunk:
    """In-memory representation of an indexed knowledge base chunk."""

    chunk_id: str
    doc_id: str
    filename: str
    doc_code: str
    title: str
    section: str
    text: str
    tokens: List[str]


class EmbeddingProvider:
    """Provides OpenAI text-embedding-3-small vectors or deterministic local embeddings."""

    DIM = 1536

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def mode(self) -> str:
        return "openai" if self.settings.has_openai_key else "local"

    @property
    def collection_name(self) -> str:
        return "ai_has_sop_openai" if self.mode == "openai" else "ai_has_sop_local"

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self.mode == "openai":
            client = OpenAI(api_key=self.settings.openai_api_key)
            response = client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=texts,
            )
            return [item.embedding for item in response.data]
        return [self._local_hash_embedding(t) for t in texts]

    def embed_query(self, query: str) -> List[float]:
        return self.embed_texts([query])[0]

    def _local_hash_embedding(self, text: str) -> List[float]:
        """Deterministic 1536-dim L2-normalized feature hashing vector (no network needed)."""
        vec = [0.0] * self.DIM
        tokens = tokenize_for_bm25(text)
        # Word unigrams + bigrams + character 3-grams for robust lexical-semantic fallback
        features: List[Tuple[str, float]] = []
        for tok in tokens:
            features.append((f"w:{tok}", 1.5))
            if len(tok) >= 4:
                for idx in range(len(tok) - 2):
                    features.append((f"c:{tok[idx:idx+3]}", 0.45))
        for i in range(len(tokens) - 1):
            features.append((f"b:{tokens[i]}_{tokens[i+1]}", 1.2))

        for feat, weight in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.DIM
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[bucket] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class KnowledgeBaseManager:
    """Manages ChromaDB persistent vector collection and synchronized BM25 index."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = EmbeddingProvider()
        chroma_dir = self.settings.resolved_chroma_dir()
        self.client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.chunks: List[IndexedChunk] = []
        self.chunk_by_id: Dict[str, IndexedChunk] = {}
        self.bm25: Optional[BM25Okapi] = None

    def _get_collection(self):
        return self.client.get_or_create_collection(
            name=self.embedder.collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )

    def _refresh_bm25_from_chroma(self) -> None:
        collection = self._get_collection()
        result = collection.get(include=["documents", "metadatas"])
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []

        loaded: List[IndexedChunk] = []
        for chunk_id, text, meta in zip(ids, docs, metas):
            meta = meta or {}
            loaded.append(
                IndexedChunk(
                    chunk_id=chunk_id,
                    doc_id=str(meta.get("doc_id", "")),
                    filename=str(meta.get("filename", "")),
                    doc_code=str(meta.get("doc_code", "SOP")),
                    title=str(meta.get("title", "")),
                    section=str(meta.get("section", "General")),
                    text=text or "",
                    tokens=tokenize_for_bm25(text or ""),
                )
            )
        self.chunks = loaded
        self.chunk_by_id = {c.chunk_id: c for c in loaded}
        self.bm25 = BM25Okapi([c.tokens for c in loaded]) if loaded else None

    @staticmethod
    def _extract_text_from_file(path: Path) -> Tuple[str, str]:
        suffix = path.suffix.lower().lstrip(".")
        if suffix in {"md", "markdown", "txt"}:
            return path.read_text(encoding="utf-8", errors="ignore"), suffix
        if suffix == "pdf":
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages), "pdf"
        raise ValueError(f"Unsupported knowledge base file type: .{suffix}")

    @staticmethod
    def _extract_metadata(filename: str, content: str) -> Tuple[str, str]:
        h1_match = _H1_RE.search(content)
        title = (
            h1_match.group(1).strip()
            if h1_match
            else filename.rsplit(".", 1)[0].replace("_", " ").title()
        )
        doc_match = _DOC_ID_RE.search(content)
        if doc_match:
            doc_code = doc_match.group(1).strip()
        else:
            stem = filename.rsplit(".", 1)[0].upper()
            doc_code = f"SOP-{stem[:16]}"
        return title, doc_code

    def _chunk_document(
        self, doc_id: str, filename: str, doc_code: str, title: str, content: str, file_type: str
    ) -> List[Tuple[str, str, Dict[str, str]]]:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings.rag_chunk_size,
            chunk_overlap=self.settings.rag_chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        sections: List[Tuple[str, str]] = []
        if file_type in {"md", "markdown"}:
            md_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
                strip_headers=False,
            )
            md_docs = md_splitter.split_text(content)
            for md_doc in md_docs:
                meta = md_doc.metadata or {}
                section_parts = [meta.get(k, "") for k in ("h2", "h3") if meta.get(k)]
                section_label = " > ".join(section_parts) if section_parts else meta.get("h1", "Overview")
                for sub_text in text_splitter.split_text(md_doc.page_content):
                    if sub_text.strip():
                        sections.append((section_label, sub_text.strip()))
        else:
            for sub_text in text_splitter.split_text(content):
                if sub_text.strip():
                    sections.append(("Document Body", sub_text.strip()))

        chunk_tuples: List[Tuple[str, str, Dict[str, str]]] = []
        for idx, (section_label, chunk_text) in enumerate(sections):
            chunk_id = f"{doc_id}::chunk_{idx:03d}"
            chunk_tuples.append(
                (
                    chunk_id,
                    chunk_text,
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "doc_code": doc_code,
                        "title": title,
                        "section": section_label[:180],
                        "chunk_index": str(idx),
                    },
                )
            )
        return chunk_tuples

    def ingest_file(self, db: Session, file_path: Path, force: bool = False) -> KBDocument:
        """Read, chunk, embed, and index a single SOP file into ChromaDB and DB registry."""
        raw_bytes = file_path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        filename = file_path.name
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", file_path.stem).lower()

        existing = db.query(KBDocument).filter(KBDocument.filename == filename).first()
        collection = self._get_collection()

        if existing and existing.content_hash == content_hash and not force:
            existing_chunks = collection.get(where={"doc_id": existing.id})
            if existing_chunks and existing_chunks.get("ids"):
                return existing

        content, file_type = self._extract_text_from_file(file_path)
        title, doc_code = self._extract_metadata(filename, content)
        chunks = self._chunk_document(
            doc_id=doc_id,
            filename=filename,
            doc_code=doc_code,
            title=title,
            content=content,
            file_type=file_type,
        )

        # Remove stale chunks for this doc_id before upserting new ones
        try:
            collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass

        if chunks:
            ids = [c[0] for c in chunks]
            texts = [c[1] for c in chunks]
            metadatas = [c[2] for c in chunks]
            embeddings = self.embedder.embed_texts(texts)
            collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=embeddings,
            )

        if existing:
            existing.title = title
            existing.doc_code = doc_code
            existing.file_type = file_type
            existing.chunk_count = len(chunks)
            existing.content_hash = content_hash
            existing.source_path = str(file_path)
            existing.indexed_at = utcnow()
            kb_doc = existing
        else:
            kb_doc = KBDocument(
                id=doc_id,
                filename=filename,
                title=title,
                doc_code=doc_code,
                file_type=file_type,
                chunk_count=len(chunks),
                content_hash=content_hash,
                source_path=str(file_path),
                indexed_at=utcnow(),
            )
            db.add(kb_doc)

        db.commit()
        db.refresh(kb_doc)
        self._refresh_bm25_from_chroma()
        return kb_doc

    def delete_document(self, db: Session, doc_id: str) -> bool:
        """Delete an SOP document from DB, ChromaDB, BM25 index, and disk."""
        kb_doc = db.query(KBDocument).filter(KBDocument.id == doc_id).first()
        if not kb_doc:
            return False

        collection = self._get_collection()
        try:
            collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            logger.warning("Failed deleting chunks from Chroma for %s: %s", doc_id, exc)

        disk_path = Path(kb_doc.source_path)
        if disk_path.exists() and disk_path.is_file():
            disk_path.unlink()

        db.delete(kb_doc)
        db.commit()
        self._refresh_bm25_from_chroma()
        return True

    def sync_knowledge_base(self, db: Session, force: bool = False) -> Dict[str, int]:
        """Synchronize all supported files in knowledge_base_dir with ChromaDB + BM25."""
        kb_dir = self.settings.resolved_kb_dir()
        supported = {".md", ".markdown", ".txt", ".pdf"}
        files = sorted(p for p in kb_dir.iterdir() if p.is_file() and p.suffix.lower() in supported)

        if force:
            # Recreate active collection cleanly on forced re-index
            try:
                self.client.delete_collection(name=self.embedder.collection_name)
            except Exception:
                pass

        indexed_docs = 0
        total_chunks = 0
        for file_path in files:
            doc = self.ingest_file(db, file_path, force=force)
            indexed_docs += 1
            total_chunks += doc.chunk_count

        self._refresh_bm25_from_chroma()
        logger.info(
            "Knowledge base synchronized: docs=%d chunks=%d embed_mode=%s",
            indexed_docs,
            len(self.chunks),
            self.embedder.mode,
        )
        return {
            "documents_indexed": indexed_docs,
            "total_chunks": len(self.chunks),
        }


_kb_manager: Optional[KnowledgeBaseManager] = None


def get_kb_manager() -> KnowledgeBaseManager:
    """Return singleton KnowledgeBaseManager instance."""
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = KnowledgeBaseManager()
    return _kb_manager


def reset_kb_manager() -> None:
    """Reset singleton KnowledgeBaseManager (used in tests or runtime config changes)."""
    global _kb_manager
    _kb_manager = None
