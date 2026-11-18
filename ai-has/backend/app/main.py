"""FastAPI application entrypoint, lifespan KB seeding, auth tokens, and webhook sandbox."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
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
from app.integrations.webhooks import verify_signature
from app.rag.ingest import get_kb_manager

settings = get_settings()


def enforce_security_config() -> None:
    """Fail fast on insecure defaults; warn loudly when demo header auth is on."""
    cfg = get_settings()
    insecure = cfg.insecure_default_secrets()
    if cfg.allow_header_role_auth:
        logging.getLogger("ai_has.main").warning(
            "DEMO MODE: ALLOW_HEADER_ROLE_AUTH=true trusts X-User-Role headers and exposes "
            "/api/auth/token. Never enable this in production."
        )
    elif insecure:
        raise RuntimeError(
            f"Refusing to start: {', '.join(insecure)} still use the shipped default value. "
            "Set strong secrets in the environment (or set ALLOW_HEADER_ROLE_AUTH=true for a local demo)."
        )
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_has.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite/PostgreSQL schema, seed default personas, and index SOP KB."""
    enforce_security_config()
    init_db()
    db = SessionLocal()
    try:
        kb_manager = get_kb_manager()
        try:
            stats = kb_manager.sync_knowledge_base(db, force=False)
        except Exception:
            if kb_manager.embedder.mode == "local":
                raise
            # A provider outage or bad key must not stop the helpdesk from starting.
            logger.exception(
                "%s embeddings failed during KB sync; falling back to local embeddings until restart",
                kb_manager.embedder.mode,
            )
            db.rollback()
            kb_manager.embedder.degraded_to_local = True
            stats = kb_manager.sync_knowledge_base(db, force=False)
        logger.info(
            "AI-HAS startup ready: llm_provider=%s embed_mode=%s kb=%s",
            settings.active_llm_provider,
            kb_manager.embedder.mode,
            stats,
        )
        if settings.active_llm_provider == "local":
            logger.warning(
                "No configured LLM provider; running local embedding + deterministic SOP/tool orchestrator."
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

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a request id to every response so client errors can be traced in logs."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(request: Request, status_code: int, detail: str, code: str, **extra: Any):
    request_id = getattr(request.state, "request_id", None)
    body: Dict[str, Any] = {"detail": detail, "code": code, "request_id": request_id, **extra}
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return _error_response(request, exc.status_code, detail, f"http_{exc.status_code}")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "field": ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")),
            "message": err.get("msg", "Invalid value"),
        }
        for err in exc.errors()
    ]
    summary = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors)
    return _error_response(
        request, 422, f"Invalid request: {summary}", "validation_error", errors=errors
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception("Unhandled error on %s %s [request_id=%s]", request.method, request.url.path, request_id)
    return _error_response(
        request, 500, "Internal server error. Please retry or contact IT with the request id.", "internal_error"
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
        "llm_provider": settings.active_llm_provider,
        "openai_configured": settings.has_openai_key,
        "gemini_configured": settings.has_gemini_key,
        "chat_model": settings.gemini_chat_model if settings.active_llm_provider == "gemini" else settings.openai_chat_model,
        "embedding_mode": kb_manager.embedder.mode,
        "kb_documents": doc_count,
        "kb_chunks": len(kb_manager.chunks),
    }


@app.post("/api/auth/token")
def issue_demo_token(req: TokenRequest) -> Dict[str, Any]:
    """Issue a signed HS256 JWT for demo personas. DEMO ONLY: disabled unless ALLOW_HEADER_ROLE_AUTH=true."""
    if not get_settings().allow_header_role_auth:
        raise HTTPException(status_code=404, detail="Not found.")
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
    """Built-in webhook receiver simulating n8n / Jira / ServiceNow ingestion.

    Verifies the HMAC-SHA256 `X-AIHAS-Signature` over the raw body and rejects bad signatures.
    """
    raw = await request.body()
    signature = request.headers.get("X-AIHAS-Signature", "")
    if not verify_signature(settings.webhook_signing_secret, raw, signature):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature.")
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook body is not valid JSON.") from exc
    if not isinstance(body, dict):
        body = {}
    return {
        "accepted": True,
        "mode": "http_sandbox_receiver",
        "external_ref": f"EXT-{uuid.uuid4().hex[:6].upper()}",
        "received_event": body.get("event_name") or request.headers.get("X-AIHAS-Event", "unknown"),
        "signature_verified": True,
    }
