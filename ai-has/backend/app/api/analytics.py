"""Operational KPI analytics, RAG evaluation stats, security audit logs, and webhook telemetry."""

from __future__ import annotations

from collections import Counter
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.rbac import AuthenticatedUser, require_roles
from app.database import AuditLog, ChatSession, MetricRecord, Ticket, WebhookDelivery, get_db

router = APIRouter(prefix="/api/analytics", tags=["Analytics & Observability"])


@router.get("/summary")
def get_analytics_summary(
    _user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Return operational KPIs: MTTR, deflection rate, latency, RAG quality, and guardrail stats."""
    chat_metrics: List[MetricRecord] = (
        db.query(MetricRecord)
        .filter(MetricRecord.metric_type == "chat_turn")
        .order_by(MetricRecord.created_at.asc())
        .all()
    )
    resolution_metrics: List[MetricRecord] = (
        db.query(MetricRecord)
        .filter(MetricRecord.metric_type == "ticket_resolution", MetricRecord.mttr_minutes.isnot(None))
        .all()
    )

    total_turns = len(chat_metrics)
    deflected_turns = sum(1 for m in chat_metrics if m.deflected)
    deflection_rate = round((deflected_turns / total_turns) * 100.0, 1) if total_turns else 0.0

    latencies = sorted(m.latency_ms for m in chat_metrics if m.latency_ms is not None)
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
    p95_idx = min(len(latencies) - 1, int(0.95 * len(latencies))) if latencies else 0
    p95_latency = round(latencies[p95_idx], 1) if latencies else 0.0

    faith_scores = [m.faithfulness_score for m in chat_metrics if m.faithfulness_score is not None]
    rel_scores = [m.relevance_score for m in chat_metrics if m.relevance_score is not None]
    avg_faithfulness = round(sum(faith_scores) / len(faith_scores), 3) if faith_scores else 0.0
    avg_relevance = round(sum(rel_scores) / len(rel_scores), 3) if rel_scores else 0.0

    mttrs = [m.mttr_minutes for m in resolution_metrics if m.mttr_minutes is not None]
    avg_mttr = round(sum(mttrs) / len(mttrs), 1) if mttrs else 0.0

    all_tickets = db.query(Ticket).all()
    open_tickets = sum(1 for t in all_tickets if t.status in {"open", "in_progress", "pending_approval"})
    escalated_tickets = sum(1 for t in all_tickets if t.status == "escalated")
    resolved_tickets = sum(1 for t in all_tickets if t.status in {"resolved", "closed"})

    pii_redactions = db.query(AuditLog).filter(AuditLog.event_type == "pii_redacted").count()
    injections_blocked = db.query(AuditLog).filter(AuditLog.event_type == "injection_blocked").count()
    rbac_denials = db.query(AuditLog).filter(AuditLog.event_type == "rbac_denied").count()
    total_sessions = db.query(ChatSession).count()

    intent_counts = Counter(m.intent or "unknown" for m in chat_metrics)
    intent_breakdown = [{"intent": k, "count": v} for k, v in intent_counts.most_common()]

    recent_series = chat_metrics[-15:]
    performance_timeseries = [
        {
            "label": f"T-{idx + 1}",
            "timestamp": m.created_at.isoformat() if m.created_at else "",
            "latency_ms": round(m.latency_ms or 0.0, 1),
            "faithfulness": round((m.faithfulness_score or 0.0) * 100.0, 1),
            "relevance": round((m.relevance_score or 0.0) * 100.0, 1),
        }
        for idx, m in enumerate(recent_series)
    ]

    return {
        "kpis": {
            "total_sessions": total_sessions,
            "total_chat_turns": total_turns,
            "deflected_turns": deflected_turns,
            "deflection_rate_pct": deflection_rate,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "avg_mttr_minutes": avg_mttr,
            "avg_faithfulness_score": avg_faithfulness,
            "avg_relevance_score": avg_relevance,
            "open_tickets": open_tickets,
            "escalated_tickets": escalated_tickets,
            "resolved_tickets": resolved_tickets,
            "pii_redactions_count": pii_redactions,
            "injections_blocked_count": injections_blocked,
            "rbac_denials_count": rbac_denials,
        },
        "intent_breakdown": intent_breakdown,
        "performance_timeseries": performance_timeseries,
    }


@router.get("/audit-logs")
def get_audit_logs(
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user: AuthenticatedUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    """Return security, guardrail, RBAC, and workflow execution audit logs (Admin only)."""
    query = db.query(AuditLog)
    if event_type:
        query = query.filter(AuditLog.event_type == event_type)
    logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {
        "logs": [
            {
                "id": log.id,
                "event_type": log.event_type,
                "severity": log.severity,
                "actor_email": log.actor_email,
                "actor_role": log.actor_role,
                "session_id": log.session_id,
                "action": log.action,
                "details": log.details_json or {},
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
    }


@router.get("/webhooks")
def get_webhook_deliveries(
    limit: int = Query(default=30, ge=1, le=100),
    _user: AuthenticatedUser = Depends(require_roles("support_agent", "admin")),
    db: Session = Depends(get_db),
):
    """Return outbound Jira/ServiceNow/n8n/SCIM webhook delivery log."""
    rows = db.query(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(limit).all()
    return {
        "deliveries": [
            {
                "id": w.id,
                "target_system": w.target_system,
                "endpoint_url": w.endpoint_url,
                "event_name": w.event_name,
                "delivery_mode": w.delivery_mode,
                "response_status": w.response_status,
                "response_body": w.response_body_json or {},
                "payload": w.payload_json or {},
                "latency_ms": w.latency_ms,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in rows
        ]
    }
