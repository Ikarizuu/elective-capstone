"""Outbound webhook dispatcher compatible with Jira, ServiceNow, and n8n/Make + local sandbox sink."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import AuditLog, WebhookDelivery


def _compute_hmac(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def _target_url_for_system(target_system: str) -> str:
    settings = get_settings()
    mapping = {
        "jira": settings.jira_webhook_url,
        "servicenow": settings.servicenow_webhook_url,
        "n8n": settings.n8n_webhook_url,
        "okta_scim": settings.n8n_webhook_url,
    }
    return (mapping.get(target_system, "") or "").strip()


async def dispatch_webhook(
    db: Session,
    target_system: str,
    event_name: str,
    payload: Dict[str, Any],
    actor_email: str = "system@acme-corp.com",
    actor_role: str = "admin",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch a signed webhook event to configured external URL or built-in sandbox sink."""
    settings = get_settings()
    target_url = _target_url_for_system(target_system)
    started = time.perf_counter()

    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_name": event_name,
        "target_system": target_system,
        "source": "ai-has-enterprise-helpdesk",
        "payload": payload,
    }
    raw_body = json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")
    signature = _compute_hmac(settings.webhook_signing_secret, raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-AIHAS-Signature": f"sha256={signature}",
        "X-AIHAS-Event": event_name,
    }

    delivery_mode = "sandbox"
    response_status = 200
    response_body: Dict[str, Any]
    endpoint_used = "local://sandbox-webhook-receiver"

    if target_url:
        delivery_mode = "live"
        endpoint_used = target_url
        try:
            async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
                resp = await client.post(target_url, content=raw_body, headers=headers)
            response_status = resp.status_code
            try:
                response_body = resp.json()
            except Exception:
                response_body = {"raw_text": resp.text[:500]}
        except Exception as exc:
            # Record error and preserve non-fatal operational fallback note
            response_status = 502
            response_body = {
                "error": str(exc),
                "fallback": "Recorded in local sandbox ledger",
            }
    else:
        external_ref = f"{target_system.upper()[:4]}-{uuid.uuid4().hex[:6].upper()}"
        response_body = {
            "accepted": True,
            "mode": "sandbox_simulator",
            "target_system": target_system,
            "external_ref": external_ref,
            "signature_verified": True,
            "message": f"Sandbox receiver processed '{event_name}' for {target_system}.",
        }

    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)

    delivery = WebhookDelivery(
        target_system=target_system,
        endpoint_url=endpoint_used,
        event_name=event_name,
        payload_json=envelope,
        response_status=response_status,
        response_body_json=response_body,
        delivery_mode=delivery_mode,
        latency_ms=latency_ms,
    )
    db.add(delivery)
    db.add(
        AuditLog(
            event_type="webhook_dispatched",
            severity="info" if response_status < 400 else "warning",
            actor_email=actor_email,
            actor_role=actor_role,
            session_id=session_id,
            action=f"Dispatched {event_name} webhook to {target_system} ({delivery_mode})",
            details_json={
                "delivery_id": delivery.id,
                "target_system": target_system,
                "status": response_status,
                "mode": delivery_mode,
                "latency_ms": latency_ms,
            },
        )
    )
    db.commit()
    db.refresh(delivery)

    return {
        "webhook_id": delivery.id,
        "target_system": target_system,
        "delivery_mode": delivery_mode,
        "status_code": response_status,
        "latency_ms": latency_ms,
        "external_ref": response_body.get("external_ref"),
        "response": response_body,
    }
