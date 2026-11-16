"""Knowledge Base management endpoints (list, hybrid search, upload, re-index, delete)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.auth.rbac import AuthenticatedUser, get_current_user, require_roles
from app.config import get_settings
from app.database import AuditLog, KBDocument, get_db
from app.rag.ingest import get_kb_manager
from app.rag.retriever import HybridRetriever

router = APIRouter(prefix="/api/kb", tags=["Knowledge Base & RAG"])

ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf"}


def _serialize_doc(doc: KBDocument) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "title": doc.title,
        "doc_code": doc.doc_code,
        "file_type": doc.file_type,
        "chunk_count": doc.chunk_count,
        "content_hash": doc.content_hash[:12],
        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
    }


@router.get("")
def list_kb_documents(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all indexed IT Standard Operating Procedure documents."""
    docs = db.query(KBDocument).order_by(KBDocument.filename.asc()).all()
    manager = get_kb_manager()
    return {
        "documents": [_serialize_doc(d) for d in docs],
        "total_documents": len(docs),
        "total_chunks": len(manager.chunks),
        "embedding_mode": manager.embedder.mode,
    }


@router.get("/search")
def search_kb(
    q: str = Query(..., min_length=2, max_length=500),
    top_k: int = Query(default=5, ge=1, le=15),
    _user: AuthenticatedUser = Depends(get_current_user),
):
    """Run hybrid vector + BM25 search over indexed SOPs."""
    retriever = HybridRetriever()
    results = retriever.retrieve(q, top_k=top_k)
    return {"query": q, "results": [r.to_dict() for r in results]}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_kb_document(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Upload and index a new Markdown, TXT, or PDF SOP document (Admin only)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing uploaded filename.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(file.filename).name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{suffix}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}.",
        )

    settings = get_settings()
    target_path = settings.resolved_kb_dir() / safe_name
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    target_path.write_bytes(content_bytes)
    manager = get_kb_manager()
    doc = manager.ingest_file(db, target_path, force=True)

    db.add(
        AuditLog(
            event_type="kb_updated",
            severity="info",
            actor_email=user.email,
            actor_role=user.role,
            action=f"Uploaded and indexed SOP document '{doc.filename}' ({doc.chunk_count} chunks)",
            details_json={"doc_id": doc.id, "doc_code": doc.doc_code, "chunks": doc.chunk_count},
        )
    )
    db.commit()
    return {"document": _serialize_doc(doc), "total_chunks": len(manager.chunks)}


@router.post("/reindex")
def reindex_kb(
    user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Force full re-chunking and re-indexing of all knowledge base files (Admin only)."""
    manager = get_kb_manager()
    stats = manager.sync_knowledge_base(db, force=True)
    db.add(
        AuditLog(
            event_type="kb_updated",
            severity="info",
            actor_email=user.email,
            actor_role=user.role,
            action="Triggered full Knowledge Base re-index",
            details_json=stats,
        )
    )
    db.commit()
    return {"status": "reindexed", **stats, "embedding_mode": manager.embedder.mode}


@router.delete("/{doc_id}")
def delete_kb_document(
    doc_id: str,
    user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Delete an SOP document from disk, ChromaDB, BM25, and DB registry (Admin only)."""
    manager = get_kb_manager()
    deleted = manager.delete_document(db, doc_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    db.add(
        AuditLog(
            event_type="kb_updated",
            severity="warning",
            actor_email=user.email,
            actor_role=user.role,
            action=f"Deleted SOP document '{doc_id}' from Knowledge Base",
            details_json={"doc_id": doc_id},
        )
    )
    db.commit()
    return {"status": "deleted", "doc_id": doc_id, "remaining_chunks": len(manager.chunks)}
