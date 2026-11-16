# SOP-01: Enterprise VPN Troubleshooting & Remote Access Standard Operating Procedure

**Document ID:** `SOP-IT-001`  
**Version:** `3.4`  
**Effective Date:** `2026-01-15`  
**Owner:** Enterprise Network & Infrastructure Operations  
**Applies To:** All Full-Time Employees, Contractors, and Remote Personnel  

---

## 1. Purpose & Scope
This Standard Operating Procedure (SOP) defines the diagnostic and resolution workflow for connecting to the corporate GlobalProtect / WireGuard Enterprise VPN (`vpn.acme-corp.internal`). All remote access to internal engineering environments, ERP systems, and confidential file shares requires an active corporate VPN tunnel authenticated via corporate SSO and hardware/app-based MFA.

---

## 2. Supported VPN Clients & Gateways
- **Primary Client:** AcmeSecure VPN Client v6.2+ (managed via MDM on Windows 11 and macOS 14+).
- **Primary Gateways:**
  - Americas East: `us-east-gw.vpn.acme-corp.internal` (Port UDP 443 / TCP 443 fallback)
  - Americas West: `us-west-gw.vpn.acme-corp.internal`
  - EMEA Frankfurt: `eu-central-gw.vpn.acme-corp.internal`
  - APAC Singapore: `ap-south-gw.vpn.acme-corp.internal`
- **Split Tunneling Policy:** Split tunneling is **disabled** for all Engineering, Finance, and HR endpoints (full tunnel enforced). Marketing and Sales laptops operate in approved SaaS-split mode.

---

## 3. Common VPN Error Codes & Tier-1 Resolution Steps

### 3.1 Error `VPN-101`: Authentication Failed / Invalid Token
- **Symptoms:** Prompt repeats endlessly or returns `Authentication rejected by identity provider (VPN-101)`.
- **Resolution Steps:**
  1. Verify that your corporate SSO password has not expired (passwords rotate every 90 days; see `SOP-IT-002`).
  2. Check your MFA authenticator clock drift. If using TOTP, resync time in the authenticator app or approve the Okta Verify push notification within **45 seconds**.
  3. Confirm you are entering `username@acme-corp.com` (full UPN), not just your short username.
  4. Wait **15 minutes** if 5 consecutive failed attempts triggered automatic temporary account lockout, or request an automated self-service password/lockout reset via the Helpdesk AI Assistant.

### 3.2 Error `VPN-204`: Gateway Unreachable / Timeout on UDP 443
- **Symptoms:** Client hangs on `Connecting to gateway...` and fails after 30 seconds with `ERR_VPN_204_TIMEOUT`.
- **Resolution Steps:**
  1. Verify general internet connectivity by loading `https://status.acme-corp.com`.
  2. Hotel, airport, or coffee-shop captive portals often block UDP 443 or intercept TLS. Open a browser to `http://neverssl.com` to complete any captive portal login first.
  3. In **AcmeSecure Client -> Settings -> Transport**, switch protocol from **UDP (Preferred)** to **TCP 443 SSL Fallback**.
  4. Try an alternate regional gateway (e.g., switch from `us-east-gw` to `us-west-gw`).
  5. Temporarily disable conflicting third-party consumer VPNs (NordVPN, ExpressVPN, Cloudflare WARP) or local proxy tools.

### 3.3 Error `VPN-309`: Internal DNS Resolution Failure After Connecting
- **Symptoms:** VPN status shows `Connected`, external websites load, but internal URLs such as `jira.acme-corp.internal` or `gitlab.acme-corp.internal` return `NXDOMAIN` or `DNS_PROBE_FINISHED_NXDOMAIN`.
- **Resolution Steps:**
  1. Flush the local DNS resolver cache:
     - **Windows (PowerShell):** Run `ipconfig /flushdns` and `Clear-DnsClientCache`.
     - **macOS (Terminal):** Run `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.
  2. Disable **DNS over HTTPS (DoH)** / Secure DNS in Chrome/Edge (`Settings -> Privacy and security -> Security -> Use secure DNS -> Off`) because browser-level DoH bypasses the corporate `10.20.0.10` internal DNS resolver.
  3. Reconnect the VPN tunnel and verify `nslookup intranet.acme-corp.internal` resolves to `10.20.4.15`.

### 3.4 Error `VPN-412`: Posture Check / Device Compliance Failed
- **Symptoms:** Connection drops immediately with `Endpoint failed HIP compliance check (VPN-412)`.
- **Causes & Resolution:**
  - Disk encryption (BitLocker on Windows / FileVault on macOS) is paused or disabled.
  - OS security patches are more than **14 days** overdue.
  - Endpoint Detection & Response (EDR / CrowdStrike Falcon) agent service is stopped.
  - **Action:** Run **Company Portal / Jamf Self Service -> Sync Device Compliance**, install pending OS updates, reboot, and retry. If CrowdStrike is missing or damaged, **escalate immediately to a Tier-2 Endpoint Security Engineer**.

---

## 4. Escalation Criteria to Human Support (Tier-2)
Escalate to a human IT Support Agent with an automatic ticket (`category: network_vpn`) when:
1. Error `VPN-412` persists after running MDM compliance sync and rebooting.
2. User requires a **Firewall Exception** or static VPN IP reservation for production SSH/database bastion access.
3. User is traveling to a **restricted region** requiring travel-exception provisioning (must be approved by InfoSec 72 hours prior to travel).
4. Three sequential Level-1 troubleshooting steps fail to establish a stable tunnel for more than 10 minutes.
