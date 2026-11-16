"""FastAPI application entrypoint, lifespan KB seeding, auth tokens, and webhook sandbox."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import analytics, chat, kb, tickets
from app.auth.rbac import (
    DEFAULT_PERSONAS,
    VALID_ROLES,
    AuthenticatedUser,
    create_access_token,
    get_current_user,
)
from app.config import get_settings
from app.database import SessionLocal, get_db, init_db
from app.rag.ingest import get_kb_manager

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_has.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite/PostgreSQL schema, seed default personas, and index SOP KB."""
    init_db()
    db = SessionLocal()
    try:
        kb_manager = get_kb_manager()
        stats = kb_manager.sync_knowledge_base(db, force=False)
        logger.info(
            "AI-HAS startup ready: openai_configured=%s embed_mode=%s kb=%s",
            settings.has_openai_key,
            kb_manager.embedder.mode,
            stats,
        )
        if not settings.has_openai_key:
            logger.warning(
                "OPENAI_API_KEY is not configured; running local embedding + deterministic SOP/tool orchestrator."
            )
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-Powered Enterprise Helpdesk & Workflow Automation System (AI-HAS)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(tickets.router)
app.include_router(kb.router)
app.include_router(analytics.router)


class TokenRequest(BaseModel):
    role: str = "employee"
    email: str | None = None
    full_name: str | None = None


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Liveness and readiness probe exposing active RAG & LLM configuration status."""
    from app.database import KBDocument

    kb_manager = get_kb_manager()
    doc_count = db.query(KBDocument).count()
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "openai_configured": settings.has_openai_key,
        "chat_model": settings.openai_chat_model,
        "embedding_mode": kb_manager.embedder.mode,
        "kb_documents": doc_count,
        "kb_chunks": len(kb_manager.chunks),
    }


@app.post("/api/auth/token")
def issue_demo_token(req: TokenRequest) -> Dict[str, Any]:
    """Issue a signed HS256 JWT token for local API testing or role-switched sessions."""
    role = req.role.lower().strip()
    if role not in VALID_ROLES:
        role = "employee"
    persona = DEFAULT_PERSONAS[role]
    email = (req.email or persona["email"]).strip().lower()
    full_name = (req.full_name or persona["full_name"]).strip()
    token = create_access_token(
        email=email,
        role=role,
        full_name=full_name,
        department=persona["department"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": role,
        "email": email,
        "full_name": full_name,
    }


@app.get("/api/auth/me")
def get_me(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Return resolved RBAC user profile for the caller."""
    return user


@app.post("/api/webhooks/sandbox-receiver")
async def sandbox_webhook_receiver(request: Request) -> Dict[str, Any]:
    """Built-in webhook receiver endpoint simulating n8n / Jira / ServiceNow ingestion."""
    body = await request.json()
    return {
        "accepted": True,
        "mode": "http_sandbox_receiver",
        "external_ref": f"EXT-{uuid.uuid4().hex[:6].upper()}",
        "received_event": body.get("event_name", "unknown"),
        "signature_header": request.headers.get("X-AIHAS-Signature", ""),
    }
