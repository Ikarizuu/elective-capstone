# SOP-03: Enterprise Software Provisioning & License Approval SOP

**Document ID:** `SOP-IT-003`  
**Version:** `2.9`  
**Effective Date:** `2026-01-20`  
**Owner:** IT Asset Management & Procurement  
**Applies To:** All Full-Time Employees and Contractors  

---

## 1. Purpose
This SOP governs how software licenses and SaaS applications are requested, evaluated against security and budget controls, and automatically provisioned via IT workflow automation (`provision_software_access` tool).

---

## 2. Software Approval Tiers

### 2.1 Tier-A: Auto-Approved Standard Productivity Software (Instant Provisioning)
Level-1 AI automation can provision these applications immediately without manager sign-off (up to standard seat quotas) and dispatch an IdP/MDM provisioning webhook:
- **Slack** (`slack`) — Standard workspace member access
- **Notion** (`notion`) — Company wiki & workspace reader/editor
- **Zoom** (`zoom`) — Standard Pro meeting license (up to 300 participants)
- **VS Code / JetBrains Toolbox Community** (`vscode`) — Approved local IDE package via MDM
- **1Password Enterprise** (`1password`) — Standard team vault access
- **Miro** (`miro`) — Viewer/Collaborator seat

### 2.2 Tier-B: Role-Gated / Manager-Approved Software (Automated Approval Ticket + Webhook)
Requests for Tier-B software require a valid **Business Justification** and trigger an automated approval workflow before license activation (SLA: **4 business hours**):
- **GitHub Enterprise** (`github`) — Requires Engineering or Product department or manager approval; includes Copilot add-on review.
- **Figma Organization** (`figma`) — Design/Product Editor seat (\$45/seat/month; Viewer is free and auto-approved).
- **Jira Software / Confluence** (`jira`, `confluence`) — Standard Project User vs. Project Admin (Admin requires Engineering Lead approval).
- **AWS / GCP Sandbox IAM Role** (`aws-sandbox`, `gcp-sandbox`) — Requires Security Awareness Training completion and Manager approval.
- **Tableau / Snowflake Reader** (`tableau`, `snowflake`) — Requires Data Governance approval and NDA check.

### 2.3 Tier-C: Restricted / Prohibited Software (Auto-Denied or InfoSec Escalation)
- **Prohibited Tools:** Unapproved consumer AI coding assistants uploading proprietary source code, cracked software, personal Dropbox/Google Drive sync clients, and torrent clients are strictly prohibited.
- **High-Privilege Tools:** Production database admin tools, root-level cloud IAM roles (`aws-prod-admin`), and SIEM admin seats **cannot** be provisioned by Level-1 Helpdesk; escalate to InfoSec & Cloud Infrastructure (`escalate_to_human`).

---

## 3. Provisioning Workflow Execution Steps
1. Verify requested `software_name` against Tier-A, Tier-B, or Tier-C classifications.
2. Confirm `access_level` (`viewer`, `standard`, `editor`, `admin`) and collect a concise **business justification** if Tier-B or elevated access (`editor`/`admin`) is requested.
3. Call `provision_software_access`:
   - **Tier-A (`standard`/`viewer`):** Status returns `provisioned` immediately, Okta/SCIM webhook fires, and license appears in user's Okta Dashboard within **5 minutes**.
   - **Tier-B or elevated role:** Status returns `pending_approval`, a Jira/ServiceNow tracking ticket is linked, and the employee's manager receives an approval notification.
4. Confirm contractor eligibility: Contractors require a sponsoring FTE email and cannot receive permanent enterprise licenses exceeding their contract end date.

---

## 4. License Reclamation Policy
Unused paid Tier-B licenses (e.g., Figma Editor, GitHub Copilot, Tableau Creator) with **zero login activity for 45 consecutive days** are automatically reclaimed during monthly IT asset sweeps after a 7-day Slack warning.
