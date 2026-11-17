"""API error-handling contract: consistent JSON shape, request ids, safe 500s."""

from __future__ import annotations

import test_rag_and_guardrails  # noqa: F401  (sets isolated env + sys.path before app import)
from fastapi.testclient import TestClient

from app.main import app

EMP = {"X-User-Role": "employee", "X-User-Email": "alex.chen@acme-corp.com"}


def test_validation_error_is_flat_and_readable():
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": ""}, headers=EMP)
    body = r.json()
    assert r.status_code == 422
    assert isinstance(body["detail"], str) and "message" in body["detail"]
    assert body["code"] == "validation_error" and body["errors"][0]["field"] == "message"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_http_error_has_code_and_request_id():
    with TestClient(app) as c:
        r = c.get("/api/chat/sessions/does-not-exist", headers=EMP)
    body = r.json()
    assert r.status_code == 404 and body["code"] == "http_404"
    assert body["detail"] and body["request_id"]


def test_request_id_is_echoed():
    with TestClient(app) as c:
        r = c.get("/api/health", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"


def test_unhandled_exception_returns_safe_500():
    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret internals")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/_boom")
    assert r.status_code == 500
    assert r.json()["code"] == "internal_error"
    assert "secret internals" not in r.text
