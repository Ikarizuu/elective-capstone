"""IT Ticket CRUD, escalation queue filtering, status transitions, and MTTR telemetry."""

from __future__ import annotations

from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.tools import VALID_CATEGORIES, _next_ticket_id
from app.auth.rbac import AuthenticatedUser, get_current_user, record_rbac_denial, require_roles
from app.database import AuditLog, MetricRecord, Ticket, get_db, utcnow
from app.guardrails.pii import scrub_pii
from app.integrations.webhooks import dispatch_webhook

router = APIRouter(prefix="/api/tickets", tags=["ITSM Tickets"])


class CreateTicketRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=250)
    description: str = Field(..., min_length=3, max_length=4000)
    category: str = Field(default="general_it")
    priority: str = Field(default="medium")


class UpdateTicketRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_agent_email: Optional[str] = None
    resolution_notes: Optional[str] = None


def _serialize_ticket(t: Ticket) -> dict:
    return {
        "id": t.id,
        "session_id": t.session_id,
        "requester_email": t.requester_email,
        "requester_name": t.requester_name,
        "title": t.title,
        "description": t.description,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "assigned_agent_email": t.assigned_agent_email,
        "ai_handoff_summary": t.ai_handoff_summary,
        "external_system": t.external_system,
        "external_ticket_ref": t.external_ticket_ref,
        "resolution_notes": t.resolution_notes,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
    }


@router.get("")
def list_tickets(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    priority: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List IT tickets scoped by RBAC (employees see own; support agents/admins see all)."""
    query = db.query(Ticket)
    if user.role == "employee":
        query = query.filter(Ticket.requester_email == user.email)
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if category:
        query = query.filter(Ticket.category == category)

    tickets = query.order_by(Ticket.created_at.desc()).all()
    return {"tickets": [_serialize_ticket(t) for t in tickets], "total": len(tickets)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ticket(
    req: CreateTicketRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an IT ticket with PII scrubbing and outbound ITSM webhook dispatch."""
    category = req.category.lower() if req.category.lower() in VALID_CATEGORIES else "general_it"
    priority = req.priority.lower() if req.priority.lower() in {"low", "medium", "high", "critical"} else "medium"
    clean_title = scrub_pii(req.title).redacted_text
    clean_desc = scrub_pii(req.description).redacted_text

    prefix = "REQ" if category in {"software_access", "general_it"} else "INC"
    ticket_id = _next_ticket_id(db, prefix=prefix)
    target_sys = "servicenow" if prefix == "INC" else "jira"

    webhook_res = await dispatch_webhook(
        db=db,
        target_system=target_sys,
        event_name="ticket.created",
        payload={
            "ticket_id": ticket_id,
            "requester_email": user.email,
            "title": clean_title,
            "category": category,
            "priority": priority,
        },
        actor_email=user.email,
        actor_role=user.role,
    )

    ticket = Ticket(
        id=ticket_id,
        requester_email=user.email,
        requester_name=user.full_name,
        title=clean_title,
        description=clean_desc,
        category=category,
        priority=priority,
        status="open",
        external_system=target_sys if webhook_res["delivery_mode"] == "live" else "sandbox",
        external_ticket_ref=webhook_res.get("external_ref") or ticket_id,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return {"ticket": _serialize_ticket(ticket), "webhook": webhook_res}


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve a single ticket by ID (RBAC-enforced)."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found.")
    if user.role == "employee" and ticket.requester_email != user.email:
        record_rbac_denial(db, user, action=f"Denied read on ticket {ticket_id}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return {"ticket": _serialize_ticket(ticket)}


@router.patch("/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    req: UpdateTicketRequest,
    user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Update ticket status, assignee, priority, or resolution notes (Agent/Admin only)."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found.")

    valid_statuses = {"open", "in_progress", "escalated", "pending_approval", "resolved", "closed"}
    valid_priorities = {"low", "medium", "high", "critical"}

    now = utcnow()
    if req.status is not None:
        new_status = req.status.lower()
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'.")
        ticket.status = new_status
        if new_status in {"resolved", "closed"} and ticket.resolved_at is None:
            ticket.resolved_at = now
            created_ref = ticket.created_at
            if created_ref.tzinfo is None:
                created_ref = created_ref.replace(tzinfo=timezone.utc)
            mttr = max(0.5, round((now - created_ref).total_seconds() / 60.0, 2))
            db.add(
                MetricRecord(
                    session_id=ticket.session_id,
                    metric_type="ticket_resolution",
                    intent=ticket.category,
                    latency_ms=0.0,
                    mttr_minutes=mttr,
                )
            )

    if req.priority is not None:
        if req.priority.lower() not in valid_priorities:
            raise HTTPException(status_code=400, detail=f"Invalid priority '{req.priority}'.")
        ticket.priority = req.priority.lower()

    if req.assigned_agent_email is not None:
        ticket.assigned_agent_email = req.assigned_agent_email

    if req.resolution_notes is not None:
        ticket.resolution_notes = req.resolution_notes

    ticket.updated_at = now
    db.add(
        AuditLog(
            event_type="ticket_updated",
            severity="info",
            actor_email=user.email,
            actor_role=user.role,
            session_id=ticket.session_id,
            action=f"Updated ticket {ticket.id} -> status={ticket.status}",
            details_json={"ticket_id": ticket.id, "status": ticket.status, "priority": ticket.priority},
        )
    )
    db.commit()
    db.refresh(ticket)

    await dispatch_webhook(
        db=db,
        target_system="servicenow",
        event_name="ticket.updated",
        payload={"ticket_id": ticket.id, "status": ticket.status, "updated_by": user.email},
        actor_email=user.email,
        actor_role=user.role,
        session_id=ticket.session_id,
    )

    return {"ticket": _serialize_ticket(ticket)}
