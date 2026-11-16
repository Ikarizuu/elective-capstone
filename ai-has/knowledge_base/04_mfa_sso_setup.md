# SOP-04: Multi-Factor Authentication (MFA) & Single Sign-On (SSO) Setup Guide

**Document ID:** `SOP-IT-004`  
**Version:** `3.2`  
**Effective Date:** `2026-01-10`  
**Owner:** Identity & Security Engineering  
**Applies To:** All Corporate Users  

---

## 1. Overview
All corporate applications—including Google Workspace, Microsoft 365, Slack, Jira, GitHub, and GlobalProtect VPN—authenticate through **Okta Enterprise SSO** (`https://sso.acme-corp.com`). Multi-Factor Authentication (MFA) is mandatory on **100% of accounts** with zero permanent exemptions.

---

## 2. Approved MFA Factors
1. **Okta FastPass / Device-Bound Passkey (Preferred):** Phishing-resistant FIDO2/WebAuthn using Touch ID, Windows Hello, or YubiKey 5 Series.
2. **YubiKey 5C / 5 NFC Hardware Token (Required for Engineering & Finance Admins):** Must register **two** hardware keys (primary + backup) when accessing production cloud or payroll systems.
3. **Okta Verify Mobile Push / TOTP (Standard Fallback):** Supported on iOS 16+ and Android 13+ with device PIN/biometric lock enabled.
4. **SMS / Voice Call OTP (Deprecated / Emergency Only):** Disabled for all administrative and engineering roles; permitted only for 24-hour onboarding grace periods.

---

## 3. New Device Enrollment & Phone Replacement Procedure
If an employee gets a **new smartphone** and still has access to their **old phone or registered YubiKey**:
1. Sign in to `https://sso.acme-corp.com` using your existing MFA factor.
2. Open **Account Settings -> Security Methods -> Okta Verify -> Set Up Another Device**.
3. Scan the enrollment QR code from the new Okta Verify mobile app, then click **Remove** on the old device.

If an employee **lost their phone and has NO backup MFA factor**:
- **Do NOT attempt self-service MFA reset** (blocked by security policy).
- Open or escalate an **Identity Verification Ticket** (`priority: high`, `category: iam_mfa`).
- A Support Agent must verify a government-issued photo ID or live video call with the employee's direct manager before issuing a **24-hour temporary enrollment bypass code**.

---

## 4. Common SSO & MFA Errors

### 4.1 `SSO-403`: App Assigned Error ("User is not assigned to this application")
- **Cause:** The user authenticated cleanly via Okta, but lacks the Okta group membership for the target SaaS app.
- **Resolution:** Run software access check (`SOP-IT-003`). For Tier-A apps, provision via `provision_software_access`. For Tier-B apps, log or verify an approval ticket.

### 4.2 `MFA-ERR-TIME`: TOTP Codes Always Rejected
- **Cause:** Mobile device clock is out of sync by more than 30 seconds.
- **Resolution:** Enable **Set Automatically** under Date & Time settings on iOS/Android, wait 30 seconds for the next 6-digit code rotation, and retry.

### 4.3 `SSO-LOOP`: Infinite Redirect Loop in Browser
- **Cause:** Stale SAML assertion cookies or third-party cookie blocking in Incognito/Safari.
- **Resolution:**
  1. Clear cookies specifically for `acme-corp.com`, `okta.com`, and the target app domain.
  2. Disable strict tracker-blocking extensions on `*.acme-corp.com`.
  3. Test in a fresh browser profile and verify system clock accuracy.
