"""OpenAI Function Calling schemas and enterprise IT automation tool handlers."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.auth.rbac import AuthenticatedUser, record_rbac_denial
from app.database import AuditLog, ChatSession, Ticket, utcnow
from app.integrations.webhooks import dispatch_webhook
from app.rag.retriever import HybridRetriever

TIER_A_SOFTWARE = {
    "slack": "Slack Enterprise Workspace",
    "notion": "Notion Corporate Wiki",
    "zoom": "Zoom Pro",
    "vscode": "Visual Studio Code (MDM)",
    "vs code": "Visual Studio Code (MDM)",
    "jetbrains": "JetBrains Toolbox Community",
    "1password": "1Password Enterprise",
    "miro": "Miro Collaborator",
}

TIER_B_SOFTWARE = {
    "github": "GitHub Enterprise",
    "figma": "Figma Organization",
    "jira": "Jira Software",
    "confluence": "Confluence Enterprise",
    "aws-sandbox": "AWS Sandbox IAM Role",
    "gcp-sandbox": "GCP Sandbox IAM Role",
    "tableau": "Tableau Creator/Reader",
    "snowflake": "Snowflake Data Warehouse",
}

TIER_C_PROHIBITED = {
    "aws-prod-admin",
    "prod-db-root",
    "dropbox-personal",
    "torrent",
    "utorrent",
    "qbittorrent",
    "siem-admin",
}

VALID_CATEGORIES = {
    "network_vpn",
    "iam_password",
    "software_access",
    "iam_mfa",
    "hardware",
    "email_messaging",
    "general_it",
    "security",
}


OPENAI_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_it_knowledge_base",
            "description": (
                "Search Acme Corp IT Standard Operating Procedures (SOPs) using hybrid vector + BM25 search "
                "for troubleshooting steps, error codes, SLAs, or policies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Targeted IT search query or error code (e.g., 'VPN-412 compliance').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_password_reset",
            "description": (
                "Initiate a secure self-service password reset link for Corporate Okta SSO, Active Directory, or VPN "
                "per SOP-IT-002. Employees may only reset their own account."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_email": {
                        "type": "string",
                        "description": "Target corporate email address (or 'self' for the authenticated employee).",
                    },
                    "system": {
                        "type": "string",
                        "enum": ["okta", "active_directory", "vpn"],
                        "description": "Target identity system for the password reset.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason (e.g., 'forgot password', '90-day expiry', 'lockout').",
                    },
                },
                "required": ["employee_email", "system", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "provision_software_access",
            "description": (
                "Evaluate and provision software access per SOP-IT-003. Tier-A tools auto-provision immediately; "
                "Tier-B or elevated access creates a manager approval ticket; Tier-C tools are denied/escalated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "software_name": {
                        "type": "string",
                        "description": "Requested application (e.g., 'slack', 'notion', 'zoom', 'github', 'figma', 'jira').",
                    },
                    "access_level": {
                        "type": "string",
                        "enum": ["viewer", "standard", "editor", "admin"],
                        "description": "Requested permission tier.",
                    },
                    "business_justification": {
                        "type": "string",
                        "description": "Brief business justification for the software license.",
                    },
                },
                "required": ["software_name", "access_level", "business_justification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_it_ticket",
            "description": (
                "Create an IT incident or service request ticket in the enterprise ITSM ledger and dispatch "
                "an outbound Jira/ServiceNow webhook."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Concise summary of the IT issue or request."},
                    "description": {"type": "string", "description": "Detailed symptoms, device info, or request context."},
                    "category": {
                        "type": "string",
                        "enum": [
                            "network_vpn",
                            "iam_password",
                            "software_access",
                            "iam_mfa",
                            "hardware",
                            "email_messaging",
                            "general_it",
                            "security",
                        ],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                },
                "required": ["title", "description", "category", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Escalate the current conversation to a live human IT Support Agent with an AI-generated handoff summary "
                "for complex issues, hardware theft, SEC-LOCK holds, lost MFA without backup, or user request for a human."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why this issue requires human intervention."},
                    "category": {
                        "type": "string",
                        "enum": [
                            "network_vpn",
                            "iam_password",
                            "software_access",
                            "iam_mfa",
                            "hardware",
                            "email_messaging",
                            "general_it",
                            "security",
                        ],
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "handoff_summary": {
                        "type": "string",
                        "description": "Structured summary of user symptoms, SOP steps tried, and recommended next steps for the agent.",
                    },
                },
                "required": ["reason", "category", "priority", "handoff_summary"],
            },
        },
    },
]


@dataclass
class ToolContext:
    """Execution context passed safely to every agent tool handler."""

    db: Session
    user: AuthenticatedUser
    session_id: str
    extracted_emails: List[str] = field(default_factory=list)


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "[REDACTED_EMAIL]"
    local, domain = email.split("@", 1)
    prefix = local[:2] if len(local) >= 2 else local[:1]
    return f"{prefix}***@{domain}"


def _next_ticket_id(db: Session, prefix: str = "INC") -> str:
    existing_count = db.query(Ticket).count()
    seq = 1001 + existing_count
    candidate = f"{prefix}-{seq}"
    while db.query(Ticket).filter(Ticket.id == candidate).first() is not None:
        seq += 1
        candidate = f"{prefix}-{seq}"
    return candidate


async def execute_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    ctx: ToolContext,
) -> Dict[str, Any]:
    """Dispatch and execute an authorized AI-HAS workflow tool with RBAC and audit logging."""
    if tool_name == "search_it_knowledge_base":
        query = str(arguments.get("query", "")).strip()
        retriever = HybridRetriever()
        results = retriever.retrieve(query, top_k=4)
        return {
            "action_type": "kb_search",
            "status": "success",
            "query": query,
            "matches": [
                {
                    "doc_code": r.doc_code,
                    "title": r.title,
                    "section": r.section,
                    "score": r.score,
                    "excerpt": r.excerpt,
                }
                for r in results
            ],
        }

    if tool_name == "trigger_password_reset":
        return await _handle_password_reset(arguments, ctx)

    if tool_name == "provision_software_access":
        return await _handle_software_provisioning(arguments, ctx)

    if tool_name == "create_it_ticket":
        return await _handle_create_ticket(arguments, ctx)

    if tool_name == "escalate_to_human":
        return await _handle_escalate_to_human(arguments, ctx)

    return {
        "action_type": "unknown",
        "status": "error",
        "error": f"Unknown tool '{tool_name}'.",
    }


async def _handle_password_reset(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    raw_target = str(arguments.get("employee_email", "")).strip().lower()
    system = str(arguments.get("system", "okta")).strip().lower()
    reason = str(arguments.get("reason", "Self-service password reset")).strip()

    if system not in {"okta", "active_directory", "vpn"}:
        system = "okta"

    # Resolve redacted placeholder or 'self' safely
    resolved_target = ctx.user.email.lower()
    if raw_target and raw_target not in {"self", "[redacted_email]", "me"}:
        resolved_target = raw_target
    elif ctx.extracted_emails:
        resolved_target = ctx.extracted_emails[0].lower()

    # RBAC rule per SOP-IT-002 §4: employees can only reset their own account
    if ctx.user.role == "employee" and resolved_target != ctx.user.email.lower():
        record_rbac_denial(
            ctx.db,
            ctx.user,
            action="Denied cross-account password reset attempt",
            session_id=ctx.session_id,
            details={"requested_for": _mask_email(resolved_target), "system": system},
        )
        return {
            "action_type": "password_reset",
            "status": "denied",
            "system": system,
            "reason": (
                "Per SOP-IT-002 §4, standard employees can only initiate password resets for their own "
                f"authenticated account ({_mask_email(ctx.user.email)})."
            ),
        }

    # Policy check per SOP-IT-002 §5: SEC-LOCK or privileged -adm accounts must escalate
    lower_reason = reason.lower()
    if "-adm" in resolved_target or "sec-lock" in lower_reason or "executive" in lower_reason:
        return {
            "action_type": "password_reset",
            "status": "escalation_required",
            "system": system,
            "reason": (
                "Per SOP-IT-002 §5, privileged (-adm), executive, or SEC-LOCK flagged accounts "
                "cannot use automated Level-1 password reset. Escalation to a human Support Agent is required."
            ),
        }

    webhook_res = await dispatch_webhook(
        db=ctx.db,
        target_system="okta_scim",
        event_name="iam.password_reset.requested",
        payload={
            "target_email_masked": _mask_email(resolved_target),
            "system": system,
            "delivery_channel": "secondary_recovery_email",
            "expires_in_minutes": 15,
            "requested_by": ctx.user.email,
            "reason": reason,
        },
        actor_email=ctx.user.email,
        actor_role=ctx.user.role,
        session_id=ctx.session_id,
    )

    ctx.db.add(
        AuditLog(
            event_type="tool_executed",
            severity="info",
            actor_email=ctx.user.email,
            actor_role=ctx.user.role,
            session_id=ctx.session_id,
            action=f"Triggered {system} password reset link for {_mask_email(resolved_target)}",
            details_json={
                "tool": "trigger_password_reset",
                "system": system,
                "webhook_id": webhook_res["webhook_id"],
            },
        )
    )
    ctx.db.commit()

    return {
        "action_type": "password_reset",
        "status": "success",
        "system": system,
        "target_email": _mask_email(resolved_target),
        "delivery_channel": "secondary_recovery_email",
        "expires_in_minutes": 15,
        "webhook_id": webhook_res["webhook_id"],
        "external_ref": webhook_res.get("external_ref"),
        "delivery_mode": webhook_res["delivery_mode"],
    }


async def _handle_software_provisioning(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    raw_name = str(arguments.get("software_name", "")).strip()
    software_key = raw_name.lower()
    access_level = str(arguments.get("access_level", "standard")).strip().lower()
    justification = str(arguments.get("business_justification", "Operational role requirement")).strip()

    if access_level not in {"viewer", "standard", "editor", "admin"}:
        access_level = "standard"

    # Match canonical key
    matched_tier = "unknown"
    display_name = raw_name or "Requested Application"

    for k, label in TIER_A_SOFTWARE.items():
        if k in software_key:
            matched_tier = "tier_a"
            display_name = label
            break

    if matched_tier == "unknown":
        for k, label in TIER_B_SOFTWARE.items():
            if k in software_key:
                matched_tier = "tier_b"
                display_name = label
                break

    if any(p in software_key for p in TIER_C_PROHIBITED) or "prod-admin" in software_key or "root" in software_key:
        matched_tier = "tier_c"

    if matched_tier == "tier_c":
        ctx.db.add(
            AuditLog(
                event_type="tool_executed",
                severity="warning",
                actor_email=ctx.user.email,
                actor_role=ctx.user.role,
                session_id=ctx.session_id,
                action=f"Blocked Tier-C prohibited software provisioning request: {display_name}",
                details_json={"tool": "provision_software_access", "software": display_name},
            )
        )
        ctx.db.commit()
        return {
            "action_type": "software_provisioning",
            "status": "denied",
            "tier": "Tier-C (Restricted / Prohibited)",
            "software": display_name,
            "access_level": access_level,
            "reason": (
                "Per SOP-IT-003 §2.3, restricted or prohibited software cannot be provisioned by Level-1 automation. "
                "Please escalate to InfoSec / Cloud Infrastructure if a business exception is needed."
            ),
        }

    # Tier-A auto-provisioning for viewer/standard
    if matched_tier == "tier_a" and access_level in {"viewer", "standard"}:
        ticket_id = _next_ticket_id(ctx.db, prefix="REQ")
        webhook_res = await dispatch_webhook(
            db=ctx.db,
            target_system="okta_scim",
            event_name="software.access.provisioned",
            payload={
                "ticket_id": ticket_id,
                "user_email": ctx.user.email,
                "software": display_name,
                "access_level": access_level,
                "tier": "Tier-A",
            },
            actor_email=ctx.user.email,
            actor_role=ctx.user.role,
            session_id=ctx.session_id,
        )
        ticket = Ticket(
            id=ticket_id,
            session_id=ctx.session_id,
            requester_email=ctx.user.email,
            requester_name=ctx.user.full_name,
            title=f"Auto-Provisioned {display_name} ({access_level})",
            description=f"Business Justification: {justification}",
            category="software_access",
            priority="low",
            status="resolved",
            external_system=webhook_res["delivery_mode"],
            external_ticket_ref=webhook_res.get("external_ref") or ticket_id,
            resolution_notes=f"Auto-approved Tier-A software via SOP-IT-003 §2.1.",
            resolved_at=utcnow(),
        )
        ctx.db.add(ticket)
        ctx.db.add(
            AuditLog(
                event_type="tool_executed",
                severity="info",
                actor_email=ctx.user.email,
                actor_role=ctx.user.role,
                session_id=ctx.session_id,
                action=f"Auto-provisioned Tier-A software {display_name} ({access_level})",
                details_json={"ticket_id": ticket_id, "webhook_id": webhook_res["webhook_id"]},
            )
        )
        ctx.db.commit()
        return {
            "action_type": "software_provisioning",
            "status": "provisioned",
            "tier": "Tier-A (Auto-Approved)",
            "software": display_name,
            "access_level": access_level,
            "ticket_id": ticket_id,
            "eta": "Active in Okta Dashboard within 5 minutes",
            "webhook_id": webhook_res["webhook_id"],
            "external_ref": webhook_res.get("external_ref"),
            "delivery_mode": webhook_res["delivery_mode"],
        }

    # Tier-B or elevated access requires manager approval workflow
    ticket_id = _next_ticket_id(ctx.db, prefix="REQ")
    webhook_res = await dispatch_webhook(
        db=ctx.db,
        target_system="jira",
        event_name="software.approval.requested",
        payload={
            "ticket_id": ticket_id,
            "user_email": ctx.user.email,
            "software": display_name,
            "access_level": access_level,
            "justification": justification,
            "sla_hours": 4,
        },
        actor_email=ctx.user.email,
        actor_role=ctx.user.role,
        session_id=ctx.session_id,
    )
    ticket = Ticket(
        id=ticket_id,
        session_id=ctx.session_id,
        requester_email=ctx.user.email,
        requester_name=ctx.user.full_name,
        title=f"Software Access Request: {display_name} ({access_level})",
        description=f"Business Justification: {justification}",
        category="software_access",
        priority="medium",
        status="pending_approval",
        external_system="jira" if webhook_res["delivery_mode"] == "live" else "sandbox",
        external_ticket_ref=webhook_res.get("external_ref") or ticket_id,
    )
    ctx.db.add(ticket)
    ctx.db.add(
        AuditLog(
            event_type="tool_executed",
            severity="info",
            actor_email=ctx.user.email,
            actor_role=ctx.user.role,
            session_id=ctx.session_id,
            action=f"Submitted Tier-B manager approval request {ticket_id} for {display_name}",
            details_json={"ticket_id": ticket_id, "webhook_id": webhook_res["webhook_id"]},
        )
    )
    ctx.db.commit()

    return {
        "action_type": "software_provisioning",
        "status": "pending_approval",
        "tier": "Tier-B (Manager Approval Required)",
        "software": display_name,
        "access_level": access_level,
        "ticket_id": ticket_id,
        "sla": "4 business hours",
        "webhook_id": webhook_res["webhook_id"],
        "external_ref": webhook_res.get("external_ref"),
        "delivery_mode": webhook_res["delivery_mode"],
    }


async def _handle_create_ticket(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    title = str(arguments.get("title", "IT Support Request")).strip()[:250]
    description = str(arguments.get("description", title)).strip()
    category = str(arguments.get("category", "general_it")).strip().lower()
    priority = str(arguments.get("priority", "medium")).strip().lower()

    if category not in VALID_CATEGORIES:
        category = "general_it"
    if priority not in {"low", "medium", "high", "critical"}:
        priority = "medium"

    prefix = "REQ" if category in {"software_access", "general_it"} else "INC"
    ticket_id = _next_ticket_id(ctx.db, prefix=prefix)
    target_sys = "servicenow" if prefix == "INC" else "jira"

    webhook_res = await dispatch_webhook(
        db=ctx.db,
        target_system=target_sys,
        event_name="ticket.created",
        payload={
            "ticket_id": ticket_id,
            "requester_email": ctx.user.email,
            "title": title,
            "category": category,
            "priority": priority,
        },
        actor_email=ctx.user.email,
        actor_role=ctx.user.role,
        session_id=ctx.session_id,
    )

    ticket = Ticket(
        id=ticket_id,
        session_id=ctx.session_id,
        requester_email=ctx.user.email,
        requester_name=ctx.user.full_name,
        title=title,
        description=description,
        category=category,
        priority=priority,
        status="open",
        external_system=target_sys if webhook_res["delivery_mode"] == "live" else "sandbox",
        external_ticket_ref=webhook_res.get("external_ref") or ticket_id,
    )
    ctx.db.add(ticket)
    ctx.db.add(
        AuditLog(
            event_type="tool_executed",
            severity="info",
            actor_email=ctx.user.email,
            actor_role=ctx.user.role,
            session_id=ctx.session_id,
            action=f"Created IT ticket {ticket_id} ({category}, {priority})",
            details_json={"ticket_id": ticket_id, "webhook_id": webhook_res["webhook_id"]},
        )
    )
    ctx.db.commit()

    return {
        "action_type": "ticket_created",
        "status": "open",
        "ticket_id": ticket_id,
        "title": title,
        "category": category,
        "priority": priority,
        "webhook_id": webhook_res["webhook_id"],
        "external_ref": webhook_res.get("external_ref"),
        "delivery_mode": webhook_res["delivery_mode"],
    }


async def _handle_escalate_to_human(arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    reason = str(arguments.get("reason", "Complex IT issue requires Tier-2 human review.")).strip()
    category = str(arguments.get("category", "general_it")).strip().lower()
    priority = str(arguments.get("priority", "high")).strip().lower()
    handoff_summary = str(arguments.get("handoff_summary", reason)).strip()

    if category not in VALID_CATEGORIES:
        category = "general_it"
    if priority not in {"low", "medium", "high", "critical"}:
        priority = "high"

    session = ctx.db.query(ChatSession).filter(ChatSession.id == ctx.session_id).first()
    if session:
        session.status = "escalated"
        session.handoff_summary = handoff_summary
        session.deflected = False
        session.updated_at = utcnow()

    existing_ticket = (
        ctx.db.query(Ticket)
        .filter(Ticket.session_id == ctx.session_id, Ticket.status.in_(["open", "escalated", "in_progress"]))
        .first()
    )

    if existing_ticket:
        existing_ticket.status = "escalated"
        existing_ticket.priority = priority
        existing_ticket.category = category
        existing_ticket.ai_handoff_summary = handoff_summary
        existing_ticket.updated_at = utcnow()
        ticket_id = existing_ticket.id
    else:
        ticket_id = _next_ticket_id(ctx.db, prefix="INC")
        ticket = Ticket(
            id=ticket_id,
            session_id=ctx.session_id,
            requester_email=ctx.user.email,
            requester_name=ctx.user.full_name,
            title=f"Escalated: {reason[:180]}",
            description=reason,
            category=category,
            priority=priority,
            status="escalated",
            ai_handoff_summary=handoff_summary,
            external_system="sandbox",
            external_ticket_ref=f"ITSD-{uuid.uuid4().hex[:4].upper()}",
        )
        ctx.db.add(ticket)

    webhook_res = await dispatch_webhook(
        db=ctx.db,
        target_system="n8n",
        event_name="session.escalated_to_human",
        payload={
            "ticket_id": ticket_id,
            "session_id": ctx.session_id,
            "requester_email": ctx.user.email,
            "category": category,
            "priority": priority,
            "handoff_summary": handoff_summary,
        },
        actor_email=ctx.user.email,
        actor_role=ctx.user.role,
        session_id=ctx.session_id,
    )

    ctx.db.add(
        AuditLog(
            event_type="human_takeover",
            severity="high" if priority in {"high", "critical"} else "info",
            actor_email=ctx.user.email,
            actor_role=ctx.user.role,
            session_id=ctx.session_id,
            action=f"Escalated session to Tier-2 human queue ({ticket_id})",
            details_json={
                "ticket_id": ticket_id,
                "priority": priority,
                "category": category,
                "webhook_id": webhook_res["webhook_id"],
            },
        )
    )
    ctx.db.commit()

    return {
        "action_type": "human_escalation",
        "status": "escalated",
        "ticket_id": ticket_id,
        "session_id": ctx.session_id,
        "priority": priority,
        "category": category,
        "handoff_summary": handoff_summary,
        "queue": "Tier-2 Live Support Queue",
        "webhook_id": webhook_res["webhook_id"],
        "external_ref": webhook_res.get("external_ref"),
        "delivery_mode": webhook_res["delivery_mode"],
    }
