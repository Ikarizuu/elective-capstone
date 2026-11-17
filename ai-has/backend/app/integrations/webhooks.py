"""Outbound webhook dispatcher with native Jira / ServiceNow / n8n payloads, HMAC signing and retries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import AuditLog, WebhookDelivery

logger = logging.getLogger("ai_has.webhooks")

_JIRA_PRIORITY = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Highest"}
# ServiceNow urgency/impact: 1 = High, 2 = Medium, 3 = Low
_SNOW_URGENCY = {"low": "3", "medium": "2", "high": "1", "critical": "1"}
_SNOW_IMPACT = {"low": "3", "medium": "2", "high": "2", "critical": "1"}


def _compute_hmac(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def sign_body(secret: str, body: bytes) -> str:
    """Return the `X-AIHAS-Signature` header value for a raw request body."""
    return f"sha256={_compute_hmac(secret, body)}"


def verify_signature(secret: str, body: bytes, header_value: Optional[str]) -> bool:
    """Constant-time verification of an `X-AIHAS-Signature` header against a raw body."""
    if not header_value:
        return False
    return hmac.compare_digest(sign_body(secret, body), header_value.strip())


def _target_url_for_system(target_system: str) -> str:
    settings = get_settings()
    mapping = {
        "jira": settings.jira_webhook_url,
        "servicenow": settings.servicenow_webhook_url,
        "n8n": settings.n8n_webhook_url,
        "okta_scim": settings.n8n_webhook_url,
    }
    return (mapping.get(target_system, "") or "").strip()


def _make_client(timeout: float) -> httpx.AsyncClient:
    """Client factory (a seam so tests can inject an `httpx.MockTransport`)."""
    return httpx.AsyncClient(timeout=timeout)


def _describe_event(event_name: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    """Return a (summary, description) pair for ticketing-system payloads."""
    summary = (
        payload.get("title")
        or payload.get("software") and f"Software access: {payload['software']}"
        or event_name
    )
    lines = [f"{k}: {v}" for k, v in payload.items() if v not in (None, "", [])]
    return str(summary)[:240], "\n".join(lines)


def build_request_body(target_system: str, event_name: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Map the internal event envelope to the native body expected by each target system."""
    settings = get_settings()
    payload: Dict[str, Any] = envelope.get("payload", {})
    priority = str(payload.get("priority", "medium")).lower()
    summary, description = _describe_event(event_name, payload)
    description = f"{description}\n\nAI-HAS event: {event_name} ({envelope.get('event_id')})"

    if target_system == "jira":
        return {
            "fields": {
                "project": {"key": settings.jira_project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": "Task" if "approval" in event_name else "Incident"},
                "priority": {"name": _JIRA_PRIORITY.get(priority, "Medium")},
                "labels": ["ai-has", event_name.replace(".", "-")],
            }
        }

    if target_system == "servicenow":
        return {
            "short_description": summary,
            "description": description,
            "caller_id": payload.get("requester_email") or payload.get("user_email", ""),
            "category": payload.get("category", "inquiry"),
            "urgency": _SNOW_URGENCY.get(priority, "2"),
            "impact": _SNOW_IMPACT.get(priority, "2"),
            "correlation_id": envelope.get("event_id"),
        }

    # n8n / Make / SCIM style automation hooks consume the full envelope.
    return envelope


def _auth_header_for_system(target_system: str) -> str:
    settings = get_settings()
    if target_system == "jira":
        return settings.jira_authorization.strip()
    if target_system == "servicenow":
        return settings.servicenow_authorization.strip()
    return ""


def _extract_external_ref(target_system: str, body: Dict[str, Any]) -> Optional[str]:
    """Pull the remote record id out of a live response (Jira `key`, ServiceNow `result.number`)."""
    if not isinstance(body, dict):
        return None
    if body.get("external_ref"):
        return str(body["external_ref"])
    if target_system == "jira" and body.get("key"):
        return str(body["key"])
    if target_system == "servicenow":
        result = body.get("result")
        if isinstance(result, dict) and (result.get("number") or result.get("sys_id")):
            return str(result.get("number") or result.get("sys_id"))
    return None


async def _post_with_retries(
    url: str, content: bytes, headers: Dict[str, str]
) -> Tuple[int, Dict[str, Any], int]:
    """POST with exponential backoff. Retries timeouts/network errors and 5xx; never retries 4xx."""
    settings = get_settings()
    max_attempts = 1 + settings.webhook_max_retries
    status_code = 502
    body: Dict[str, Any] = {}
    attempt = 0

    while attempt < max_attempts:
        attempt += 1
        retryable = False
        try:
            async with _make_client(settings.webhook_timeout_seconds) as client:
                resp = await client.post(url, content=content, headers=headers)
            status_code = resp.status_code
            try:
                parsed = resp.json()
                body = parsed if isinstance(parsed, dict) else {"data": parsed}
            except Exception:
                body = {"raw_text": resp.text[:500]}
            retryable = status_code >= 500
        except httpx.HTTPError as exc:
            status_code = 502
            body = {"error": str(exc) or exc.__class__.__name__, "fallback": "Recorded in local ledger"}
            retryable = True

        if not retryable or attempt >= max_attempts:
            break
        delay = settings.webhook_retry_backoff_seconds * (2 ** (attempt - 1))
        logger.warning("Webhook to %s failed (status %s); retry %d/%d in %.2fs", url, status_code, attempt, max_attempts - 1, delay)
        if delay > 0:
            await asyncio.sleep(delay)

    return status_code, body, attempt


async def dispatch_webhook(
    db: Session,
    target_system: str,
    event_name: str,
    payload: Dict[str, Any],
    actor_email: str = "system@acme-corp.com",
    actor_role: str = "admin",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch a signed webhook event to the configured external URL or the built-in sandbox sink."""
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
    request_body = build_request_body(target_system, event_name, envelope)
    raw_body = json.dumps(request_body, sort_keys=True, default=str).encode("utf-8")
    signature = sign_body(settings.webhook_signing_secret, raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-AIHAS-Signature": signature,
        "X-AIHAS-Event": event_name,
    }
    auth_header = _auth_header_for_system(target_system)
    if auth_header:
        headers["Authorization"] = auth_header

    delivery_mode = "sandbox"
    response_status = 200
    attempts = 1
    response_body: Dict[str, Any]
    endpoint_used = "local://sandbox-webhook-receiver"

    if target_url:
        delivery_mode = "live"
        endpoint_used = target_url
        response_status, response_body, attempts = await _post_with_retries(target_url, raw_body, headers)
    else:
        external_ref = f"{target_system.upper()[:4]}-{uuid.uuid4().hex[:6].upper()}"
        response_body = {
            "accepted": True,
            "mode": "sandbox_simulator",
            "target_system": target_system,
            "external_ref": external_ref,
            "signature_verified": verify_signature(settings.webhook_signing_secret, raw_body, signature),
            "message": f"Sandbox receiver processed '{event_name}' for {target_system}.",
        }

    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    external_ref_value = _extract_external_ref(target_system, response_body)
    response_body = {**response_body, "attempts": attempts}

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
                "attempts": attempts,
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
        "attempts": attempts,
        "latency_ms": latency_ms,
        "external_ref": external_ref_value,
        "response": response_body,
    }
