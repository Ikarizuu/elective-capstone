"""Gemini mode: function calling via google-genai, driven by a scripted fake client.

No API key or network is needed. Like test_openai_mode.py, every chat test checks
llm_mode, because any provider exception falls back to the deterministic engine.
Also covers resilience when the provider is down: actions that already ran are
reported (not hidden or repeated), the backend still starts if embeddings fail,
and search degrades to BM25 when query embedding fails.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import test_rag_and_guardrails  # noqa: F401  (sets isolated env + sys.path before app import)
from fastapi.testclient import TestClient
from google import genai
from google.genai import types

from app.config import get_settings
from app.database import SessionLocal, WebhookDelivery
from app.main import app
from app.rag.ingest import EmbeddingProvider, get_kb_manager
from app.rag.retriever import HybridRetriever

EMPLOYEE = {"X-User-Role": "employee", "X-User-Email": "alex.chen@acme-corp.com"}
FAKE_KEY = "fake-gemini-key-for-unit-tests"


# ---------------------------------------------------------------------------
# Fake google-genai client
# ---------------------------------------------------------------------------


def _response(function_calls, text, content):
    return SimpleNamespace(
        function_calls=function_calls,
        text=text,
        candidates=[SimpleNamespace(content=content)],
        usage_metadata=SimpleNamespace(total_token_count=120),
    )


def g_call(name: str, args: dict):
    content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
    return _response([SimpleNamespace(name=name, args=args)], None, content)


def g_text(text: str):
    return _response(None, text, types.Content(role="model", parts=[types.Part(text=text)]))


class FakeModels:
    """Returns scripted responses in order and records every request it receives."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture()
def gemini_mode(monkeypatch):
    """Turn on Gemini chat orchestration with a fake client; keep embeddings local."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "llm_provider", "gemini")
    monkeypatch.setattr(cfg, "gemini_api_key", FAKE_KEY)
    # Gemini mode would also embed via the real API; keep retrieval offline.
    monkeypatch.setattr(EmbeddingProvider, "mode", property(lambda self: "local"))

    def install(*script) -> FakeModels:
        models = FakeModels(script)
        client = SimpleNamespace(aio=SimpleNamespace(models=models))
        monkeypatch.setattr(genai, "Client", lambda **_kwargs: client)
        return models

    return install


def _chat(client: TestClient, message: str) -> dict:
    resp = client.post("/api/chat", json={"message": message}, headers=EMPLOYEE)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _webhook_count() -> int:
    db = SessionLocal()
    try:
        return db.query(WebhookDelivery).count()
    finally:
        db.close()


RESET_ARGS = {"employee_email": "self", "system": "okta", "reason": "Forgot password"}


# ---------------------------------------------------------------------------
# Chat orchestration
# ---------------------------------------------------------------------------


def test_tool_call_round_trip_executes_tool_and_returns_final_answer(gemini_mode):
    fake = gemini_mode(
        g_call("trigger_password_reset", RESET_ARGS),
        g_text("Done. A reset link was sent to your registered device [SOP-IT-002]."),
    )
    with TestClient(app) as c:
        data = _chat(c, "I forgot my Okta password, please reset it.")

    assert data["llm_mode"] == "gemini"
    assert data["intent"] == "tool_password_reset"
    assert data["reply"] == "Done. A reset link was sent to your registered device [SOP-IT-002]."
    [call] = data["tool_calls"]
    assert (call["tool_name"], call["status"]) == ("trigger_password_reset", "success")

    # Round 1 offers the tools; round 2 receives the tool result as a function response.
    assert len(fake.requests) == 2
    declared = {d.name for t in fake.requests[0]["config"].tools for d in t.function_declarations}
    assert {"trigger_password_reset", "escalate_to_human"} <= declared
    reply_part = fake.requests[1]["contents"][-1].parts[0].function_response
    assert reply_part.name == "trigger_password_reset"
    assert reply_part.response["result"]["status"] == "success"


def test_rbac_still_applies_when_the_model_requests_another_users_reset(gemini_mode):
    gemini_mode(
        g_call("trigger_password_reset", {**RESET_ARGS, "employee_email": "maya.patel@acme-corp.com"}),
        g_text("I can't reset another employee's password."),
    )
    with TestClient(app) as c:
        data = _chat(c, "Reset my coworker Maya's Okta password for her.")

    assert data["llm_mode"] == "gemini"
    [call] = data["tool_calls"]
    assert (call["tool_name"], call["status"]) == ("trigger_password_reset", "denied")


def test_plain_question_is_grounded_cited_and_persisted(gemini_mode):
    answer = "Run the GlobalProtect posture check again after updating macOS [SOP-IT-001]."
    fake = gemini_mode(g_text(answer))
    with TestClient(app) as c:
        data = _chat(c, "My GlobalProtect VPN fails with error VPN-412 after a macOS update.")
        session = c.get(f"/api/chat/sessions/{data['session_id']}", headers=EMPLOYEE).json()

    assert data["llm_mode"] == "gemini"
    assert data["intent"] == "rag_qa"
    assert data["citations"] and data["citations"][0]["doc_code"] == "SOP-IT-001"
    assert "SOP-IT-001" in str(fake.requests[0]["config"].system_instruction)
    assistant = [m for m in session["messages"] if m["sender_role"] == "assistant"]
    assert assistant[-1]["content"] == answer


def test_pii_is_scrubbed_before_reaching_the_model(gemini_mode):
    fake = gemini_mode(g_text("I can help with that [SOP-IT-002]."))
    with TestClient(app) as c:
        data = _chat(c, "My SSN is 123-45-6789 and my phone is (415) 555-0192. Reset my Okta password.")

    assert data["llm_mode"] == "gemini"
    sent = str(fake.requests[0]["contents"])
    assert "123-45-6789" not in sent
    assert "555-0192" not in sent


def test_gemini_failure_falls_back_visibly_and_is_logged(gemini_mode, caplog):
    gemini_mode(RuntimeError("simulated Gemini 503"))
    with caplog.at_level(logging.ERROR, logger="ai_has.orchestrator"), TestClient(app) as c:
        data = _chat(c, "My GlobalProtect VPN fails with error VPN-412.")

    assert data["llm_mode"] == "deterministic_fallback"
    assert data["reply"]
    failures = [r for r in caplog.records if r.name == "ai_has.orchestrator" and r.exc_info]
    assert failures and "simulated Gemini 503" in str(failures[0].exc_info[1])


def test_actions_already_taken_are_reported_not_hidden_or_repeated(gemini_mode):
    gemini_mode(g_call("trigger_password_reset", RESET_ARGS), RuntimeError("outage on round 2"))
    with TestClient(app) as c:
        before = _webhook_count()
        data = _chat(c, "I forgot my Okta password, please reset it.")
        sent = _webhook_count() - before

    assert data["llm_mode"] == "deterministic_fallback"
    assert data["intent"] == "tool_password_reset"
    assert [t["tool_name"] for t in data["tool_calls"]] == ["trigger_password_reset"]
    assert "Password reset" in data["reply"]
    assert sent == 1, "the reset must run exactly once, not again via the fallback"


# ---------------------------------------------------------------------------
# Resilience when the provider is down
# ---------------------------------------------------------------------------


def test_backend_starts_on_local_embeddings_when_provider_embeddings_fail(monkeypatch, caplog):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "llm_provider", "gemini")
    monkeypatch.setattr(cfg, "gemini_api_key", FAKE_KEY)
    # Registering the flag with monkeypatch restores it after the test.
    monkeypatch.setattr(get_kb_manager().embedder, "degraded_to_local", False)

    def embed_content(**_kwargs):
        raise RuntimeError("simulated Gemini embedding outage")

    monkeypatch.setattr(
        genai, "Client", lambda **_kwargs: SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    )
    with caplog.at_level(logging.ERROR, logger="ai_has.main"), TestClient(app) as c:
        health = c.get("/api/health").json()

    assert health["status"] == "healthy"
    assert health["llm_provider"] == "gemini"  # chat still targets Gemini
    assert health["embedding_mode"] == "local"  # search degraded, not dead
    assert health["kb_chunks"] > 0
    assert any("falling back to local embeddings" in r.getMessage() for r in caplog.records)


def test_search_uses_bm25_when_query_embedding_fails(monkeypatch):
    with TestClient(app):  # make sure the KB is indexed
        kb = get_kb_manager()

        def embed_query(_query):
            raise RuntimeError("simulated embedding outage")

        monkeypatch.setattr(kb.embedder, "embed_query", embed_query)
        results = HybridRetriever(kb).retrieve("GlobalProtect VPN error VPN-412 posture check", top_k=3)

    assert results, "search returned nothing when the vector side failed"
    assert results[0].doc_code == "SOP-IT-001"
    assert results[0].vector_score == 0.0
