# SOP-02: Enterprise Password Reset & Account Lockout Policy

**Document ID:** `SOP-IT-002`  
**Version:** `4.1`  
**Effective Date:** `2026-02-01`  
**Owner:** Identity & Access Management (IAM) Team  
**Applies To:** All Employees, Contractors, and Helpdesk Operators  

---

## 1. Purpose
This policy establishes secure identity verification, password complexity requirements, account lockout thresholds, and automated self-service password reset procedures across Corporate Active Directory (AD) and Okta Single Sign-On (SSO).

---

## 2. Password Complexity & Lifecycle Standards
- **Minimum Length:** **14 characters** for standard employee accounts; **20 characters** for privileged/admin accounts (`-adm` suffix).
- **Character Requirements:** Must include at least 3 of the 4 categories: uppercase (`A-Z`), lowercase (`a-z`), numeric (`0-9`), and special symbol (`!@#$%^&*`).
- **Prohibited Content:** Cannot contain the user's first name, last name, username, `acme`, or any of the last **12 passwords** used.
- **Rotation Cadence:** Standard user passwords expire every **90 days**. Service accounts use rotated secrets in HashiCorp Vault and never use interactive passwords.

---

## 3. Account Lockout Rules
- **Threshold:** **5 consecutive invalid login attempts** within a 15-minute window locks the SSO/AD account.
- **Automatic Lockout Duration:** **15 minutes** (auto-unlocks after cooldown unless flagged as suspicious brute-force activity by InfoSec SIEM).
- **Suspicious Lockout Indicator:** If logins originate from an anomalous ASN/country or exceed 20 attempts per minute, the account enters **Security Hold (`SEC-LOCK`)**, which **cannot** be unlocked via self-service and must be escalated to InfoSec SOC.

---

## 4. Automated Password Reset Workflow (Level-1 AI & Self-Service)
Authorized Level-1 Helpdesk automation (`trigger_password_reset` tool) can initiate a secure password reset link **only under the following conditions**:
1. **Target System:** Corporate SSO (`Okta`), Active Directory (`AD`), or `VPN` account.
2. **Scope Restriction:** Standard employees may request a reset **only for their own account** (`employee_email` matching authenticated user identity). Support agents and admins may trigger resets for verified users after confirming identity.
3. **Delivery Channel:** Reset links are sent strictly to the user's registered **secondary recovery email** or verified **SMS/Push MFA token** — never pasted directly in chat and never containing plaintext temporary passwords.
4. **Link Expiration:** Password reset tokens remain valid for **15 minutes** and allow single use only.
5. **Post-Reset Requirement:** After resetting the corporate SSO password, macOS FileVault / Windows cached credentials require updating by logging into the workstation while connected to the corporate network or signing into VPN with the new password and locking/unlocking the screen.

---

## 5. Forbidden Actions & Escalation Rules
- **NEVER** accept, ask for, store, or repeat a user's current password, MFA backup seed, or recovery codes in chat.
- **NEVER** reset passwords for **Executive Leadership**, **Domain Admins**, or accounts flagged with **`SEC-LOCK`** via Level-1 automation — escalate immediately to a human Support Agent (`priority: high`).
- If a user loses access to both their primary device and MFA authenticator simultaneously, **do not** issue an automated reset; create a high-priority identity verification ticket requiring live video verification with the user's manager and IT Support.
