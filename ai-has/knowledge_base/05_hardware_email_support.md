# SOP-05: Corporate Hardware, Peripherals & Enterprise Email Support

**Document ID:** `SOP-IT-005`  
**Version:** `2.6`  
**Effective Date:** `2026-02-12`  
**Owner:** Workplace Technology & Endpoint Support  
**Applies To:** All Employees  

---

## 1. Corporate Laptop Lifecycle & Hardware Replacement
- **Standard Issued Hardware:**
  - Engineering / Design: MacBook Pro 14" or 16" (M3/M4 Pro, 36 GB RAM) or Lenovo ThinkPad P1 (Ubuntu/Windows 11).
  - Business / Operations: MacBook Air 15" (M3, 24 GB RAM) or Dell Latitude 7450 (Windows 11).
- **Refresh Cycle:** Eligible for standard refresh every **36 months** (3 years).
- **Damaged, Stolen, or Unbootable Laptop:**
  - **Lost or Stolen Device:** Must be reported **within 1 hour** by escalating immediately (`priority: critical`) so IT Security can trigger remote MDM wipe (`Jamf` / `Intune`) and revoke all OAuth/SSO tokens.
  - **Battery Swelling / Liquid Spill / Screen Fracture:** Power down immediately, do not charge, and log a hardware replacement ticket (`category: hardware`) with shipping address and asset tag (e.g., `ACME-LT-XXXX`). Loaner laptops ship via overnight courier (SLA: **24 business hours** in US/EU hubs).

---

## 2. Peripherals & Home Office Stipend
- Every full-time employee receives a one-time **$750 USD** ergonomic peripheral allowance (monitor, dock, mechanical keyboard, mouse, headset) expensed via Coupa under cost center `IT-WFX-100`.
- Replacement USB-C chargers, Thunderbolt docks, and YubiKey 5C tokens are stocked by Workplace IT and dispatched upon creating a standard `hardware` ticket.

---

## 3. Enterprise Email (Google Workspace / Outlook) Troubleshooting

### 3.1 Quota & Large Attachments
- Standard corporate mailbox quota is **100 GB** (unlimited archive for Legal/Compliance).
- Maximum email attachment size is **25 MB**; larger files must be shared via Google Drive or OneDrive with restricted internal permissions.

### 3.2 Bounce Error `550 5.7.1` / SPF-DKIM Quarantine
- **Cause:** Sending to external domains with invalid recipient address or triggered DLP (Data Loss Prevention) rule (e.g., unencrypted SSNs, API keys, or bulk customer CSVs).
- **Resolution:**
  1. Remove sensitive PII or unencrypted secrets and send via approved Secure File Transfer (`vault.acme-corp.com`).
  2. If a legitimate business email is wrongly quarantined by Proofpoint, log a `email_messaging` ticket containing the message timestamp and recipient domain (never paste sensitive body content into the ticket).

### 3.3 Shared Mailboxes & Distribution Lists (`dl-*`)
- Creating a new corporate Google Group / Distribution List (`dl-teamname@acme-corp.com`) requires manager approval and a designated primary + backup owner.
- Adding or removing members on existing department lists takes up to **60 minutes** to propagate via Okta group sync.

---

## 4. Escalation SLA Summary
| Priority | Scope Examples | Target Response SLA | Target Resolution SLA |
| :--- | :--- | :--- | :--- |
| **Critical (`P1`)** | Lost/stolen laptop, suspected account compromise (`SEC-LOCK`), company-wide outage | 15 minutes | 2 hours |
| **High (`P2`)** | Unbootable laptop, locked out of both phone + MFA, persistent `VPN-412` blocking work | 30 minutes | 4 hours |
| **Medium (`P3`)** | Tier-B software request, peripheral replacement, email quarantine review | 2 business hours | 1 business day |
| **Low (`P4`)** | How-to questions, ergonomic stipend inquiry, DL ownership update | 4 business hours | 3 business days |
