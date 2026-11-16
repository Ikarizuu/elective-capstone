# AI-HAS Enterprise User Manual

Use the **Role Switcher** in the top navigation bar to switch between **Employee**, **Support Agent**, and **Admin** personas.

---

## 1. Employee Guide (`/`)
The Employee Portal provides instant Level-1 IT resolution backed by approved company SOPs.

### Asking Routine IT Questions (Grounded Hybrid RAG)
1. Type questions about VPN errors (`VPN-101`, `VPN-204`, `VPN-309`, `VPN-412`), password rules, Okta MFA enrollment, hardware stipends, or email bounces.
2. AI-HAS streams its response in real time with **inline SOP citation badges** (e.g., `SOP-IT-001`).
3. Click any citation pill beneath a message to inspect the exact SOP section, confidence score, and source excerpt.

### Triggering Automated IT Workflows
- **Password Resets:** Ask *"I forgot my Okta password, please trigger a reset."* An Action Status Card displays the masked target email, delivery channel, 15-minute link expiration, and workflow reference.
- **Software Provisioning:**
  - **Tier-A** (`Slack`, `Notion`, `Zoom`, `VS Code`, `1Password`, `Miro` standard access) provisions instantly via Okta/SCIM webhook.
  - **Tier-B** (`GitHub`, `Figma Editor`, `Jira`, `AWS Sandbox`, `Snowflake`) opens a manager-approval ticket (`REQ-XXXX`) with a 4-hour SLA.
- **Ticket Creation:** Request a hardware replacement or peripheral dispatch to log an ITSM ticket (`INC-XXXX` / `REQ-XXXX`) visible in the **My IT Tickets** sidebar.

### Security Guardrails & Escalation
- **PII Scrubbing:** If you accidentally paste an SSN, phone number, IP address, API key, or password, AI-HAS automatically redacts it (`[REDACTED_SSN]`, `[REDACTED_SECRET]`) before processing or saving.
- **Human Escalation:** Click **Escalate to Human** or ask to speak with an agent (or report a lost/stolen laptop) to hand off your conversation to a live Support Agent.

---

## 2. Support Agent Guide (`/agent`)
Switch to **Role: Support Agent (`Maya Patel`)** to operate the Human-in-the-Loop Live Queue.

1. **Review Escalation Queue:** Inspect incoming escalated threads sorted by update time alongside linked Ticket IDs and Priority badges.
2. **Read AI Handoff Summary:** Every escalation includes a concise **AI-Generated Handoff Summary** detailing symptoms, user context, and referenced SOPs.
3. **Take Over Live Chat:** Click **Take Over Live Chat** to assume control. Automated AI replies pause for that session, and your messages stream into the employee's chat window.
4. **Resolve & Record MTTR:** Enter optional resolution notes and click **Resolve & Close** to close the session, resolve the linked ticket, and record Mean Time to Resolution (MTTR).
5. **Manage Ticket Queue:** Update any ticket's status (`open`, `in_progress`, `pending_approval`, `resolved`, `closed`) from the bottom ITSM table.

---

## 3. Admin Guide (`/admin`)
Switch to **Role: Admin (`Jordan Taylor`)** to monitor system KPIs, security guardrails, and the SOP Knowledge Base.

1. **Operational KPIs & Charts:** Track Ticket Deflection Rate, Mean Time to Resolution (MTTR), Avg/P95 Response Latency, RAG Faithfulness/Relevance percentages, and Intent Breakdown.
2. **Knowledge Base Manager:**
   - Upload new `.md`, `.txt`, or `.pdf` SOP files for instant chunking and ChromaDB + BM25 indexing.
   - Run **Test Search** to inspect hybrid Vector vs. BM25 scores on live queries.
   - Click **Re-index KB** after editing SOP files on disk, or delete obsolete SOP documents.
3. **Security Audit Logs & Webhook Ledger:** Audit every `pii_redacted`, `injection_blocked`, `rbac_denied`, `tool_executed`, `human_takeover`, and `kb_updated` event, and inspect signed Jira/ServiceNow/n8n/SCIM webhook deliveries.
