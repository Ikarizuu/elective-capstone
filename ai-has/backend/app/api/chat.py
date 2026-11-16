"""Chat streaming, session history, escalation, and human-in-the-loop takeover endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.orchestrator import run_chat_turn, stream_chat_turn
from app.auth.rbac import AuthenticatedUser, get_current_user, record_rbac_denial, require_roles
from app.database import AuditLog, ChatMessage, ChatSession, MetricRecord, Ticket, get_db, utcnow
from app.guardrails.pii import scrub_pii
from app.integrations.webhooks import dispatch_webhook

router = APIRouter(prefix="/api/chat", tags=["Chat & Human-in-the-Loop"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None


class EscalateRequest(BaseModel):
    reason: str = Field(default="User requested human support specialist", min_length=3, max_length=1000)
    priority: str = Field(default="high")


class AgentMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ResolveSessionRequest(BaseModel):
    resolution_notes: str = Field(default="Resolved by Support Agent", max_length=2000)


def _serialize_message(msg: ChatMessage) -> dict:
    return {
        "id": msg.id,
        "session_id": msg.session_id,
        "sender_role": msg.sender_role,
        "sender_email": msg.sender_email,
        "content": msg.content,
        "pii_redacted": msg.pii_redacted,
        "citations": msg.citations_json or [],
        "tool_calls": msg.tool_calls_json or [],
        "intent": msg.intent,
        "latency_ms": msg.latency_ms,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def _serialize_session(session: ChatSession, include_messages: bool = False) -> dict:
    payload = {
        "id": session.id,
        "user_email": session.user_email,
        "title": session.title,
        "status": session.status,
        "assigned_agent_email": session.assigned_agent_email,
        "handoff_summary": session.handoff_summary,
        "deflected": session.deflected,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "resolved_at": session.resolved_at.isoformat() if session.resolved_at else None,
        "message_count": len(session.messages) if session.messages is not None else 0,
    }
    if include_messages:
        payload["messages"] = [_serialize_message(m) for m in session.messages]
    return payload


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream AI-HAS guardrail, citation, tool-calling, and token events over SSE."""
    if req.session_id:
        existing = db.query(ChatSession).filter(ChatSession.id == req.session_id).first()
        if existing and user.role == "employee" and existing.user_email != user.email:
            record_rbac_denial(
                db,
                user,
                action="Employee attempted to post to another user's chat session",
                session_id=req.session_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this chat session.",
            )

    return StreamingResponse(
        stream_chat_turn(db=db, user=user, message=req.message, session_id=req.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("")
async def chat_sync(
    req: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Synchronous JSON chat endpoint running the identical guarded AI-HAS pipeline."""
    if req.session_id:
        existing = db.query(ChatSession).filter(ChatSession.id == req.session_id).first()
        if existing and user.role == "employee" and existing.user_email != user.email:
            record_rbac_denial(
                db,
                user,
                action="Employee attempted to post to another user's chat session",
                session_id=req.session_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this chat session.",
            )
    return await run_chat_turn(db=db, user=user, message=req.message, session_id=req.session_id)


@router.get("/sessions")
def list_sessions(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=30, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List chat sessions scoped by RBAC (employees see own; agents/admins see all)."""
    query = db.query(ChatSession)
    if user.role == "employee":
        query = query.filter(ChatSession.user_email == user.email)
    if status_filter:
        query = query.filter(ChatSession.status == status_filter)
    sessions = query.order_by(ChatSession.updated_at.desc()).limit(limit).all()
    return {"sessions": [_serialize_session(s) for s in sessions]}


@router.get("/escalated")
def list_escalated_queue(
    user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Return active human-in-the-loop escalation queue for Support Agents and Admins."""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.status.in_(["escalated", "agent_active"]))
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    items = []
    for s in sessions:
        linked_ticket = (
            db.query(Ticket)
            .filter(Ticket.session_id == s.id)
            .order_by(Ticket.created_at.desc())
            .first()
        )
        data = _serialize_session(s, include_messages=True)
        data["ticket"] = (
            {
                "id": linked_ticket.id,
                "priority": linked_ticket.priority,
                "category": linked_ticket.category,
                "status": linked_ticket.status,
            }
            if linked_ticket
            else None
        )
        items.append(data)
    return {"queue": items}


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full message transcript and citations for a chat session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    if user.role == "employee" and session.user_email != user.email:
        record_rbac_denial(
            db,
            user,
            action="Denied cross-user chat session read",
            session_id=session_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    return _serialize_session(session, include_messages=True)


@router.post("/sessions/{session_id}/escalate")
async def escalate_session(
    session_id: str,
    req: EscalateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually escalate a session to the live Support Agent queue with an AI handoff summary."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if user.role == "employee" and session.user_email != user.email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    from app.agent.tools import ToolContext, execute_tool_call

    recent_user = [m.content for m in session.messages if m.sender_role == "user"][-3:]
    summary = (
        f"Escalated by {user.full_name} ({user.email}). Reason: {req.reason}. "
        f"Recent context: {' | '.join(recent_user) if recent_user else 'No prior messages.'}"
    )
    ctx = ToolContext(db=db, user=user, session_id=session.id)
    res = await execute_tool_call(
        "escalate_to_human",
        {
            "reason": req.reason,
            "category": "general_it",
            "priority": req.priority if req.priority in {"low", "medium", "high", "critical"} else "high",
            "handoff_summary": summary,
        },
        ctx,
    )
    sys_msg = ChatMessage(
        session_id=session.id,
        sender_role="system",
        content=f"Session escalated to Human Support Queue (Ticket {res['ticket_id']}).",
        intent="escalate",
        tool_calls_json=[{"tool_name": "escalate_to_human", "result": res, "status": "escalated"}],
    )
    db.add(sys_msg)
    db.commit()
    return {"status": "escalated", "ticket_id": res["ticket_id"], "handoff_summary": summary}


@router.post("/sessions/{session_id}/takeover")
async def takeover_session(
    session_id: str,
    user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Support Agent assumes live human-in-the-loop control of an escalated chat session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    session.status = "agent_active"
    session.assigned_agent_email = user.email
    session.deflected = False
    session.updated_at = utcnow()

    linked_ticket = (
        db.query(Ticket)
        .filter(Ticket.session_id == session.id)
        .order_by(Ticket.created_at.desc())
        .first()
    )
    if linked_ticket:
        linked_ticket.status = "in_progress"
        linked_ticket.assigned_agent_email = user.email
        linked_ticket.updated_at = utcnow()

    notice = ChatMessage(
        session_id=session.id,
        sender_role="system",
        sender_email=user.email,
        content=f"{user.full_name} ({user.email}) joined the conversation and took over from AI.",
        intent="human_takeover",
    )
    db.add(notice)
    db.add(
        AuditLog(
            event_type="human_takeover",
            severity="info",
            actor_email=user.email,
            actor_role=user.role,
            session_id=session.id,
            action=f"Support agent {user.email} took over session {session.id}",
            details_json={"ticket_id": linked_ticket.id if linked_ticket else None},
        )
    )
    db.commit()

    await dispatch_webhook(
        db=db,
        target_system="n8n",
        event_name="session.agent_takeover",
        payload={"session_id": session.id, "agent_email": user.email},
        actor_email=user.email,
        actor_role=user.role,
        session_id=session.id,
    )
    db.refresh(session)
    return _serialize_session(session, include_messages=True)


@router.post("/sessions/{session_id}/agent-message")
def post_agent_message(
    session_id: str,
    req: AgentMessageRequest,
    user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Send a live human support agent message into an escalated or taken-over session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    if session.status not in {"agent_active", "escalated"}:
        session.status = "agent_active"
    if not session.assigned_agent_email:
        session.assigned_agent_email = user.email

    cleaned = scrub_pii(req.message, redact_emails=False)
    msg = ChatMessage(
        session_id=session.id,
        sender_role="support_agent",
        sender_email=user.email,
        content=cleaned.redacted_text,
        pii_redacted=cleaned.was_redacted,
        intent="human_agent_reply",
    )
    session.updated_at = utcnow()
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"message": _serialize_message(msg), "session_status": session.status}


@router.post("/sessions/{session_id}/resolve")
def resolve_session(
    session_id: str,
    req: ResolveSessionRequest,
    user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Mark an active/escalated session and its linked ticket resolved and record MTTR."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    now = utcnow()
    session.status = "closed"
    session.resolved_at = now
    session.updated_at = now

    created_ref = session.created_at
    if created_ref.tzinfo is None:
        from datetime import timezone
        created_ref = created_ref.replace(tzinfo=timezone.utc)
    mttr_minutes = max(0.5, round((now - created_ref).total_seconds() / 60.0, 2))

    linked_ticket = (
        db.query(Ticket)
        .filter(Ticket.session_id == session.id)
        .order_by(Ticket.created_at.desc())
        .first()
    )
    if linked_ticket:
        linked_ticket.status = "resolved"
        linked_ticket.resolution_notes = req.resolution_notes
        linked_ticket.assigned_agent_email = linked_ticket.assigned_agent_email or user.email
        linked_ticket.resolved_at = now
        linked_ticket.updated_at = now

    db.add(
        ChatMessage(
            session_id=session.id,
            sender_role="system",
            sender_email=user.email,
            content=f"Session closed and resolved by {user.full_name}: {req.resolution_notes}",
            intent="session_resolved",
        )
    )
    db.add(
        MetricRecord(
            session_id=session.id,
            metric_type="ticket_resolution",
            intent="human_resolution",
            latency_ms=0.0,
            mttr_minutes=mttr_minutes,
            deflected=False,
            escalated=True,
        )
    )
    db.commit()
    return {"status": "closed", "mttr_minutes": mttr_minutes, "ticket_id": linked_ticket.id if linked_ticket else None}
