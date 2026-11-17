# AI-HAS REST & Server-Sent Events (SSE) API Specification

**Base URL:** `http://localhost:8000`

## Authentication & RBAC Headers
All protected endpoints support:
> `ALLOW_HEADER_ROLE_AUTH` is **demo-only** and defaults to `false`; with it off, header auth returns `401` and `POST /api/auth/token` returns `404`.

1. **JWT Bearer Token:** `Authorization: Bearer <token>` (minted via `POST /api/auth/token`)
2. **Enterprise Dev Headers** (when `ALLOW_HEADER_ROLE_AUTH=true`):
   - `X-User-Role`: `employee` | `support_agent` | `admin`
   - `X-User-Email`: Corporate email address
   - `X-User-Name`: Display name

---

## 1. System & Authentication Endpoints

### `GET /api/health`
Returns service readiness, OpenAI status, embedding mode, and Knowledge Base counts.
```json
{
  "status": "healthy",
  "service": "AI-HAS Enterprise Helpdesk",
  "version": "1.0.0",
  "openai_configured": true,
  "chat_model": "gpt-4o-mini",
  "embedding_mode": "openai",
  "kb_documents": 5,
  "kb_chunks": 43
}
```

### `POST /api/auth/token`
Mints a signed HS256 JWT token for API callers.
- **Request:** `{"role": "admin", "email": "jordan.taylor@acme-corp.com"}`
- **Response:** `{"access_token": "...", "token_type": "bearer", "role": "admin", "email": "..."}`

### `GET /api/auth/me`
Returns the resolved `AuthenticatedUser` identity.

---

## 2. Chat, SSE Streaming & Human-in-the-Loop Endpoints

### `POST /api/chat/stream`
Streams real-time SSE frames through the guardrail, RAG, and tool-calling pipeline.
- **Roles:** `employee`, `support_agent`, `admin`
- **Request Body:**
```json
{
  "message": "My GlobalProtect VPN fails with VPN-412.",
  "session_id": null
}
```
- **SSE Event Sequence (`text/event-stream`):**
  1. `event: session` -> `{"session_id": "...", "status": "active", "title": "..."}`
  2. `event: guardrail` -> `{"pii_redacted": false, "pii_categories": [], "injection_blocked": false, "risk_score": 0.0}`
  3. `event: citations` -> `{"citations": [{"doc_code": "SOP-IT-001", "section": "...", "score": 0.89, ...}]}`
  4. `event: tool_result` *(optional)* -> `{"tool_name": "provision_software_access", "result": {...}, "status": "provisioned"}`
  5. `event: token` -> `{"delta": "Based on corporate SOPs..."}`
  6. `event: done` -> `{"message_id": "...", "session_id": "...", "session_status": "active", "intent": "rag_qa", "latency_ms": 145.2, "faithfulness_score": 0.94, "relevance_score": 0.88, "deflected": true, "llm_mode": "openai"}`

### `POST /api/chat`
Synchronous JSON equivalent of `/api/chat/stream` returning the complete turn object with `reply`, `guardrail`, `citations`, `tool_calls`, `intent`, and `latency_ms`.

### `GET /api/chat/sessions`
Lists sessions (employees see only their own; agents/admins see all). Optional query `?status=escalated`.

### `GET /api/chat/sessions/{session_id}`
Returns session metadata and full message transcript with citations and tool action cards.

### `GET /api/chat/escalated`
- **Roles:** `support_agent`, `admin`
- Returns all sessions in `escalated` or `agent_active` state alongside linked ticket summaries.

### `POST /api/chat/sessions/{session_id}/escalate`
Escalates a session to Tier-2 human support with an AI-generated handoff summary and creates/updates an escalated ticket.

### `POST /api/chat/sessions/{session_id}/takeover`
- **Roles:** `support_agent`, `admin`
- Transitions session to `agent_active`, assigns `assigned_agent_email`, updates linked ticket to `in_progress`, and logs `human_takeover`.

### `POST /api/chat/sessions/{session_id}/agent-message`
- **Roles:** `support_agent`, `admin`
- Posts a live `support_agent` reply into the session thread.

### `POST /api/chat/sessions/{session_id}/resolve`
- **Roles:** `support_agent`, `admin`
- Closes the session (`status: "closed"`), resolves the linked ticket, and computes/records `mttr_minutes`.

---

## 3. ITSM Ticket Endpoints

### `GET /api/tickets`
Lists tickets (scoped to requester for `employee`, global for `support_agent`/`admin`). Supports `?status=`, `?priority=`, `?category=`.

### `POST /api/tickets`
Creates a ticket directly, scrubs PII, and dispatches a signed webhook.

### `GET /api/tickets/{ticket_id}`
Fetches a single ticket (RBAC enforced).

### `PATCH /api/tickets/{ticket_id}`
- **Roles:** `support_agent`, `admin`
- Updates `status`, `priority`, `assigned_agent_email`, or `resolution_notes`; computes MTTR when transitioned to `resolved`/`closed`.

---

## 4. Knowledge Base & Hybrid RAG Endpoints

### `GET /api/kb`
Lists indexed SOP documents, chunk counts, and active embedding mode.

### `GET /api/kb/search?q=<query>&top_k=5`
Runs Hybrid Vector + BM25 RRF search directly and returns scored citations.

### `POST /api/kb/upload`
- **Roles:** `admin`
- Multipart form upload (`file`) supporting `.md`, `.markdown`, `.txt`, and `.pdf`. Chunks, embeds, and indexes immediately.

### `POST /api/kb/reindex`
- **Roles:** `admin`
- Forces complete re-chunking and re-indexing of all documents in `KNOWLEDGE_BASE_DIR`.

### `DELETE /api/kb/{doc_id}`
- **Roles:** `admin`
- Deletes the SOP file from disk, ChromaDB, BM25 index, and SQLite/Postgres.

---

## 5. Observability, Audit Logs & Webhook Endpoints

### `GET /api/analytics/summary`
- **Roles:** `admin`
- Returns KPIs (`deflection_rate_pct`, `avg_mttr_minutes`, `avg_latency_ms`, `p95_latency_ms`, `avg_faithfulness_score`, `avg_relevance_score`, guardrail counts), `intent_breakdown`, and `performance_timeseries`.

### `GET /api/analytics/audit-logs`
- **Roles:** `admin`
- Returns security, guardrail, RBAC denial, KB mutation, and workflow audit entries.

### `GET /api/analytics/webhooks`
- **Roles:** `support_agent`, `admin`
- Returns outbound Jira/ServiceNow/n8n/SCIM webhook delivery logs and sandbox simulator responses.

### `POST /api/webhooks/sandbox-receiver`
Built-in local webhook receiver endpoint for end-to-end integration testing without external credentials.
- **Headers:** `X-AIHAS-Signature: sha256=<HMAC-SHA256 of raw body>` (required) and `X-AIHAS-Event`.
- **Responses:** `200 {"accepted": true, "external_ref": "EXT-...", "received_event": "...", "signature_verified": true}`; `401` on a missing or invalid signature; `400` on non-JSON bodies.
