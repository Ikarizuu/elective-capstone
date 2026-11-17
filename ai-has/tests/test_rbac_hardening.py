"""RBAC hardening: header auth and the token minter are demo-only; default secrets fail fast."""

from __future__ import annotations

import pytest
import test_rag_and_guardrails  # noqa: F401  (sets isolated env + sys.path before app import)
from fastapi.testclient import TestClient

from app.auth.rbac import create_access_token
from app.config import get_settings
from app.main import app, enforce_security_config

ADMIN_HEADERS = {"X-User-Role": "admin"}


@pytest.fixture()
def secure_mode(monkeypatch):
    """Production-like settings: demo header auth off, strong secrets."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "allow_header_role_auth", False)
    monkeypatch.setattr(cfg, "jwt_secret", "x" * 40)
    monkeypatch.setattr(cfg, "webhook_signing_secret", "y" * 40)
    return cfg


def test_header_role_is_rejected_when_demo_mode_off(secure_mode):
    with TestClient(app) as c:
        r = c.get("/api/analytics/summary", headers=ADMIN_HEADERS)
    assert r.status_code == 401


def test_signed_jwt_still_works_when_demo_mode_off(secure_mode):
    token = create_access_token(email="jordan.taylor@acme-corp.com", role="admin")
    with TestClient(app) as c:
        r = c.get("/api/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_token_minter_is_hidden_when_demo_mode_off(secure_mode):
    with TestClient(app) as c:
        r = c.post("/api/auth/token", json={"role": "admin"})
    assert r.status_code == 404


def test_token_minter_works_in_demo_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_header_role_auth", True)
    with TestClient(app) as c:
        r = c.post("/api/auth/token", json={"role": "employee"})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"


def test_default_secrets_refuse_to_start_outside_demo_mode(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "allow_header_role_auth", False)
    # Shipped defaults are still in place (test env does not override them).
    assert cfg.insecure_default_secrets()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        enforce_security_config()


def test_demo_mode_only_warns_on_default_secrets(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_header_role_auth", True)
    enforce_security_config()  # must not raise


def test_strong_secrets_pass_startup_check(secure_mode):
    enforce_security_config()  # must not raise
