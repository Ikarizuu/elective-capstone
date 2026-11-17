"""Webhook dispatcher: native Jira/ServiceNow payloads, HMAC signing/verification, retries."""

from __future__ import annotations

import json

import httpx
import pytest
import test_rag_and_guardrails  # noqa: F401  (sets isolated env + sys.path before app import)
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.integrations import webhooks
from app.main import app


@pytest.fixture()
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def fast_settings(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "webhook_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(cfg, "webhook_max_retries", 2)
    monkeypatch.setattr(cfg, "jira_webhook_url", "https://jira.example.test/rest/api/2/issue")
    monkeypatch.setattr(cfg, "servicenow_webhook_url", "https://snow.example.test/api/now/table/incident")
    monkeypatch.setattr(cfg, "jira_authorization", "Basic dGVzdDp0ZXN0")
    return cfg


def _mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(webhooks, "_make_client", lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout))


async def test_jira_payload_shape_signature_and_ref(db, fast_settings, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["headers"] = request.headers
        return httpx.Response(201, json={"key": "ITSD-42"})

    _mock_transport(monkeypatch, handler)
    res = await webhooks.dispatch_webhook(
        db, "jira", "software.approval.requested",
        {"ticket_id": "REQ-1", "title": "Figma access", "priority": "critical", "user_email": "a@b.co"},
    )
    body = json.loads(seen["body"])
    assert body["fields"]["project"]["key"] == "ITSD"
    assert body["fields"]["priority"]["name"] == "Highest"
    assert body["fields"]["issuetype"]["name"] == "Task"
    assert "ai-has" in body["fields"]["labels"]
    assert seen["headers"]["authorization"] == "Basic dGVzdDp0ZXN0"
    assert webhooks.verify_signature(fast_settings.webhook_signing_secret, seen["body"], seen["headers"]["x-aihas-signature"])
    assert res["delivery_mode"] == "live" and res["external_ref"] == "ITSD-42" and res["attempts"] == 1


async def test_servicenow_payload_shape_and_ref(db, fast_settings, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"result": {"number": "INC0010001"}})

    _mock_transport(monkeypatch, handler)
    res = await webhooks.dispatch_webhook(
        db, "servicenow", "ticket.created",
        {"ticket_id": "INC-1", "title": "Laptop stolen", "priority": "critical",
         "category": "hardware", "requester_email": "alex.chen@acme-corp.com"},
    )
    assert seen["body"]["short_description"] == "Laptop stolen"
    assert seen["body"]["urgency"] == "1" and seen["body"]["impact"] == "1"
    assert seen["body"]["caller_id"] == "alex.chen@acme-corp.com"
    assert res["external_ref"] == "INC0010001"


async def test_retries_on_503_then_succeeds(db, fast_settings, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(201, json={"key": "ITSD-7"})

    _mock_transport(monkeypatch, handler)
    res = await webhooks.dispatch_webhook(db, "jira", "ticket.created", {"title": "t"})
    assert calls["n"] == 3 and res["attempts"] == 3 and res["status_code"] == 201


async def test_no_retry_on_4xx(db, fast_settings, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"errors": "bad field"})

    _mock_transport(monkeypatch, handler)
    res = await webhooks.dispatch_webhook(db, "jira", "ticket.created", {"title": "t"})
    assert calls["n"] == 1 and res["status_code"] == 400


async def test_gives_up_after_max_retries_on_network_error(db, fast_settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _mock_transport(monkeypatch, handler)
    res = await webhooks.dispatch_webhook(db, "servicenow", "ticket.created", {"title": "t"})
    assert res["status_code"] == 502 and res["attempts"] == 3


def test_sign_and_verify_roundtrip():
    body = b'{"a":1}'
    sig = webhooks.sign_body("secret", body)
    assert webhooks.verify_signature("secret", body, sig)
    assert not webhooks.verify_signature("other", body, sig)
    assert not webhooks.verify_signature("secret", b'{"a":2}', sig)
    assert not webhooks.verify_signature("secret", body, None)


def test_sandbox_receiver_rejects_bad_signature_and_accepts_good():
    cfg = get_settings()
    body = json.dumps({"event_name": "ticket.created"}).encode()
    with TestClient(app) as c:
        bad = c.post("/api/webhooks/sandbox-receiver", content=body, headers={"X-AIHAS-Signature": "sha256=deadbeef"})
        missing = c.post("/api/webhooks/sandbox-receiver", content=body)
        good = c.post(
            "/api/webhooks/sandbox-receiver", content=body,
            headers={"X-AIHAS-Signature": webhooks.sign_body(cfg.webhook_signing_secret, body)},
        )
    assert bad.status_code == 401 and missing.status_code == 401
    assert good.status_code == 200 and good.json()["signature_verified"] is True
    assert good.json()["received_event"] == "ticket.created"
