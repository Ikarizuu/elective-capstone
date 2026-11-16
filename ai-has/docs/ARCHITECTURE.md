# AI-HAS System Architecture & Design

## 1. High-Level System Architecture

The **AI-Powered Enterprise Helpdesk & Workflow Automation System (AI-HAS)** follows a layered, security-first microservice-ready architecture linking a Next.js 14+ multi-role UI, a FastAPI orchestrator, a Hybrid RAG knowledge engine (ChromaDB + BM25), OpenAI `gpt-4o-mini` function calling, and signed ITSM webhooks.

```mermaid
flowchart TB
    subgraph Client["Next.js 14+ App Router UI"]
        EMP["Employee Portal (/)"]
        AGT["Support Agent Queue (/agent)"]
        ADM["Admin Observability (/admin)"]
    end

    subgraph Security["Security & Guardrail Layer"]
        RBAC["JWT / Header RBAC"]
        INJ["Prompt Injection Detector"]
        PII["PII & Credential Scrubber"]
    end

    subgraph Core["FastAPI Orchestrator & Agent Engine"]
        SSE["SSE Streaming Chat API"]
        ORCH["Orchestrator (gpt-4o-mini / Fallback)"]
        TOOLS["Workflow Tool Executor"]
    end

    subgraph RAG["Hybrid RAG Pipeline"]
        ING["LangChain Chunker & Embedder"]
        CHROMA[("ChromaDB Persistent Vectors")]
        BM25[("BM25Okapi Lexical Index")]
        RRF["Reciprocal Rank Fusion (RRF)"]
    end

    subgraph Persistence["Storage & External Integrations"]
        DB[("SQLite / PostgreSQL (SQLAlchemy)")]
        WH["HMAC Webhook Dispatcher"]
        SINK["Sandbox Receiver / Jira / ServiceNow / n8n"]
    end

    EMP & AGT & ADM --> RBAC
    RBAC --> SSE
    SSE --> INJ --> PII --> ORCH
    ORCH --> RRF
    RRF --> CHROMA & BM25
    ING --> CHROMA & BM25
    ORCH --> TOOLS
    TOOLS --> WH --> SINK
    ORCH & TOOLS & RBAC --> DB
```

---

## 2. Hybrid RAG Pipeline & Guardrail Execution Flow

Every chat turn enforces a deterministic safety-and-grounding pipeline before any LLM call or operational workflow executes:

```mermaid
sequenceDiagram
    participant U as Employee Browser
    participant API as FastAPI SSE Endpoint
    participant INJ as Injection Guardrail
    participant PII as PII Scrubber
    participant RET as Hybrid Retriever
    participant LLM as OpenAI gpt-4o-mini
    participant WH as Webhook Dispatcher
    participant DB as SQLite/Postgres

    U->>API: POST /api/chat/stream (message + RBAC header/JWT)
    API->>INJ: inspect_prompt_injection(message)
    alt Risk Score >= 0.65
        INJ-->>DB: AuditLog (injection_blocked)
        API-->>U: SSE guardrail + refusal + done
    else Safe Input
        API->>PII: scrub_pii(message)
        PII-->>DB: Persist redacted ChatMessage + AuditLog
        API->>RET: retrieve(redacted_query, top_k=5)
        RET->>RET: ChromaDB Cosine + BM25Okapi -> RRF Fusion
        RET-->>API: Ranked SOP Citations
        API-->>U: SSE citations event
        API->>LLM: System Prompt + SOP Context + Tools
        opt Workflow Tool Triggered
            LLM-->>API: tool_call (reset / software / ticket / escalate)
            API->>WH: Dispatch signed HMAC webhook
            WH-->>DB: WebhookDelivery + AuditLog
            API-->>U: SSE tool_result ActionStatusCard
        end
        LLM-->>API: Grounded cited response
        API-->>U: SSE token stream + done (latency, faithfulness, relevance)
    end
```

---

## 3. Relational Database Schema

AI-HAS uses SQLAlchemy 2.0 models compatible with SQLite (local zero-config default) and PostgreSQL:

```mermaid
erDiagram
    users ||--o{ chat_sessions : owns
    chat_sessions ||--o{ chat_messages : contains
    chat_sessions ||--o{ tickets : escalates_to

    users {
        string id PK
        string email UK
        string full_name
        string role
        string department
        boolean is_active
        datetime created_at
    }

    chat_sessions {
        string id PK
        string user_id FK
        string user_email
        string title
        string status
        string assigned_agent_email
        text handoff_summary
        boolean deflected
        datetime created_at
        datetime updated_at
        datetime resolved_at
    }

    chat_messages {
        string id PK
        string session_id FK
        string sender_role
        string sender_email
        text content
        boolean pii_redacted
        json citations_json
        json tool_calls_json
        string intent
        float latency_ms
        datetime created_at
    }

    tickets {
        string id PK
        string session_id FK
        string requester_email
        string requester_name
        string title
        text description
        string category
        string priority
        string status
        string assigned_agent_email
        text ai_handoff_summary
        string external_system
        string external_ticket_ref
        text resolution_notes
        datetime created_at
        datetime updated_at
        datetime resolved_at
    }

    audit_logs {
        string id PK
        string event_type
        string severity
        string actor_email
        string actor_role
        string session_id
        string action
        json details_json
        datetime created_at
    }

    metrics {
        string id PK
        string session_id
        string metric_type
        string intent
        float latency_ms
        float faithfulness_score
        float relevance_score
        boolean deflected
        boolean escalated
        float mttr_minutes
        integer tokens_used
        datetime created_at
    }

    kb_documents {
        string id PK
        string filename UK
        string title
        string doc_code
        string file_type
        integer chunk_count
        string content_hash
        string source_path
        datetime indexed_at
    }

    webhook_deliveries {
        string id PK
        string target_system
        string endpoint_url
        string event_name
        json payload_json
        integer response_status
        json response_body_json
        string delivery_mode
        float latency_ms
        datetime created_at
    }
```

---

## 4. Enterprise Security Guardrails
- **RBAC Enforcement:** Every endpoint verifies either a signed `HS256` JWT Bearer token (`Authorization: Bearer <token>`) or local dev headers (`X-User-Role`, `X-User-Email`) when `ALLOW_HEADER_ROLE_AUTH=true`. Unauthorized attempts generate `rbac_denied` audit entries and HTTP `403`.
- **PII & Secret Scrubbing:** SSNs, Luhn-valid credit cards, IPv4 addresses, phone numbers, email addresses, API keys (`sk-`, `ghp_`, `AKIA`, `xox*`), and inline credentials (`password=...`) are scrubbed prior to OpenAI transmission and SQLite/PostgreSQL storage.
- **Prompt Injection Defense:** NFKC Unicode normalization, zero-width character stripping, Base64 payload decoding, and weighted regex heuristics block instruction overrides, DAN jailbreaks, template delimiter spoofing, and secret exfiltration.
