# AI-HAS Setup & Deployment Guide

## Prerequisites
- **Python:** 3.11+ (verified on 3.11–3.13)
- **Node.js:** 20+ (`npm` included)
- **Docker & Docker Compose** (optional for containerized deployment)
- **OpenAI API Key** (`OPENAI_API_KEY`) for `gpt-4o-mini` and `text-embedding-3-small` *(optional for local offline testing — AI-HAS automatically activates a local deterministic fallback engine when omitted)*

---

## 1. Environment Configuration

Copy `.env.example` to `.env` inside `ai-has/`:
```bash
cp .env.example .env
```

Key environment variables:
| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | *(empty)* | OpenAI API key enabling `gpt-4o-mini` + `text-embedding-3-small` |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Chat & function-calling model |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model for ChromaDB |
| `DATABASE_URL` | `sqlite:///./data/ai_has.db` | SQLite local DB or `postgresql+psycopg://...` |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | Local persistent ChromaDB vector directory |
| `KNOWLEDGE_BASE_DIR` | `../knowledge_base` | Directory seeded with IT SOP `.md`/`.txt`/`.pdf` files |
| `JWT_SECRET` | dev secret | Signing key for HS256 JWT tokens. **Backend refuses to start with the shipped default unless demo mode is on.** |
| `ALLOW_HEADER_ROLE_AUTH` | `false` in code; `true` in `.env.example` / `docker-compose.yml` | **DEMO ONLY.** Trusts `X-User-Role` / `X-User-Email` headers (UI role switcher) and exposes `POST /api/auth/token`. Set `false` for anything beyond a local demo. |
| `JIRA_WEBHOOK_URL` | *(blank)* | Outbound Jira webhook endpoint (uses sandbox sink if blank) |
| `SERVICENOW_WEBHOOK_URL` | *(blank)* | Outbound ServiceNow webhook endpoint |
| `N8N_WEBHOOK_URL` | *(blank)* | Outbound n8n/Make/SCIM automation webhook URL |
| `WEBHOOK_SIGNING_SECRET` | dev secret | HMAC-SHA256 signing key (`X-AIHAS-Signature`) |
| `JIRA_PROJECT_KEY` / `JIRA_AUTHORIZATION` | `ITSD` / blank | Jira project key; full `Authorization` header value (e.g. `Basic <b64>`) |
| `SERVICENOW_AUTHORIZATION` | blank | Full `Authorization` header value for ServiceNow |
| `WEBHOOK_MAX_RETRIES` / `WEBHOOK_RETRY_BACKOFF_SECONDS` | `2` / `0.5` | Retries on timeouts, network errors and 5xx (exponential backoff); 4xx is never retried |

---

## 2. Running Locally (Development)

### Step 1: Start the FastAPI Backend
```bash
cd ai-has/backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Verify backend readiness:
```bash
curl http://localhost:8000/api/health
```

### Step 2: Start the Next.js Frontend
```bash
cd ai-has/frontend
npm install
npm run dev
```
Open `http://localhost:3000` in your browser.

---

## 3. Running with Docker Compose

From `ai-has/`:
```bash
docker compose up --build
```
- Frontend UI: `http://localhost:3000`
- Backend API & OpenAPI Docs: `http://localhost:8000/docs`

---

## 4. Webhook Integration Configuration (n8n / Make / Jira / ServiceNow)
All outbound workflow events (`iam.password_reset.requested`, `software.access.provisioned`, `software.approval.requested`, `ticket.created`, `ticket.updated`, `session.escalated_to_human`, `session.agent_takeover`) include:
- Header `X-AIHAS-Signature: sha256=<hex_hmac>` computed over the sorted JSON body with `WEBHOOK_SIGNING_SECRET`.
- Header `X-AIHAS-Event: <event_name>`.

Each target receives its **native body** (signature is computed over the exact bytes sent):
- **Jira** (`JIRA_WEBHOOK_URL=https://<site>/rest/api/2/issue`): `{"fields": {"project", "summary", "description", "issuetype", "priority", "labels"}}`; the created issue `key` is stored as the external reference.
- **ServiceNow** (`SERVICENOW_WEBHOOK_URL=https://<instance>/api/now/table/incident`): Table API incident (`short_description`, `description`, `caller_id`, `category`, `urgency`, `impact`, `correlation_id`); `result.number` is stored as the external reference.
- **n8n / Make / SCIM**: the full event envelope `{event_id, event_name, target_system, source, payload}`.

Receivers should verify `X-AIHAS-Signature` (`sha256=` + HMAC-SHA256 of the raw body). The built-in receiver does this and returns `401` on a bad or missing signature.

When external URLs are left blank, AI-HAS routes deliveries to its built-in sandbox receiver and logs full envelopes in the **Admin -> Webhook Integration Ledger** tab. You can also test external HTTP delivery locally by setting `N8N_WEBHOOK_URL=http://localhost:8000/api/webhooks/sandbox-receiver`.

---

## 5. Running Tests & Load Benchmarks

### Pytest Suite (RAG, Guardrails, RBAC, Synthetic Dataset, HITL)
```bash
cd ai-has
backend/.venv/Scripts/python.exe -m pytest tests/test_rag_and_guardrails.py -v
```

### Concurrent Latency & Throughput Benchmark
With the backend running on port 8000:
```bash
python tests/load_test.py --base-url http://localhost:8000 --requests 30 --concurrency 6
```
