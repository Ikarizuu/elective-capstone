"""PII and credential redaction guardrail prior to LLM invocation and persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class PiiScanResult:
    """Structured output from PII/secret scrubbing."""

    redacted_text: str
    was_redacted: bool
    findings_count: int
    categories: List[str] = field(default_factory=list)
    extracted_emails: List[str] = field(default_factory=list)


# Ordered carefully so high-specificity secrets and SSNs match before generic phone digits.
_SECRET_PATTERNS: List[Tuple[str, re.Pattern[str], str]] = [
    (
        "api_key",
        re.compile(
            r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
        "Bearer [REDACTED_SECRET]",
    ),
    (
        "inline_credential",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b(\s*[:=]\s*)(['\"]?)([^\s'\"]{4,})\3"
        ),
        r"\1\2[REDACTED_SECRET]",
    ),
]

_SSN_PATTERN = re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")

_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

_IPV4_PATTERN = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

_PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"
)

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _luhn_valid(digits: str) -> bool:
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    reverse_digits = digits[::-1]
    for idx, ch in enumerate(reverse_digits):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scrub_pii(text: str, redact_emails: bool = True) -> PiiScanResult:
    """Scrub secrets, SSNs, credit cards, IPv4s, phone numbers, and emails from text."""
    if not text:
        return PiiScanResult(redacted_text="", was_redacted=False, findings_count=0)

    current = text
    categories: List[str] = []
    count = 0
    extracted_emails: List[str] = []

    def _record(category: str, hits: int) -> None:
        nonlocal count
        if hits > 0:
            count += hits
            if category not in categories:
                categories.append(category)

    # 1. API keys, tokens, and inline password assignments
    for category, pattern, replacement in _SECRET_PATTERNS:
        current, hits = pattern.subn(replacement, current)
        _record(category, hits)

    # 2. US Social Security Numbers
    current, ssn_hits = _SSN_PATTERN.subn("[REDACTED_SSN]", current)
    _record("ssn", ssn_hits)

    # 3. Credit card numbers (validated via Luhn checksum to avoid false positives on ticket IDs)
    cc_hits = 0

    def _replace_card(match: re.Match[str]) -> str:
        nonlocal cc_hits
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if _luhn_valid(digits):
            cc_hits += 1
            return "[REDACTED_CARD]"
        return raw

    current = _CREDIT_CARD_PATTERN.sub(_replace_card, current)
    _record("credit_card", cc_hits)

    # 4. IPv4 addresses
    current, ip_hits = _IPV4_PATTERN.subn("[REDACTED_IP]", current)
    _record("ip_address", ip_hits)

    # 5. Phone numbers
    current, phone_hits = _PHONE_PATTERN.subn("[REDACTED_PHONE]", current)
    _record("phone", phone_hits)

    # 6. Email addresses (preserve extracted list in-memory for authorized self-service context)
    def _replace_email(match: re.Match[str]) -> str:
        addr = match.group(0)
        extracted_emails.append(addr.lower())
        return "[REDACTED_EMAIL]" if redact_emails else addr

    if redact_emails:
        current, email_hits = _EMAIL_PATTERN.subn(_replace_email, current)
        _record("email", email_hits)
    else:
        extracted_emails.extend(m.lower() for m in _EMAIL_PATTERN.findall(current))

    return PiiScanResult(
        redacted_text=current,
        was_redacted=count > 0,
        findings_count=count,
        categories=categories,
        extracted_emails=extracted_emails,
    )


def scrub_dict(data: Dict) -> Dict:
    """Recursively scrub string values inside an audit/log payload dictionary."""
    cleaned: Dict = {}
    for key, val in data.items():
        if isinstance(val, str):
            cleaned[key] = scrub_pii(val).redacted_text
        elif isinstance(val, dict):
            cleaned[key] = scrub_dict(val)
        elif isinstance(val, list):
            cleaned[key] = [
                scrub_pii(item).redacted_text
                if isinstance(item, str)
                else scrub_dict(item)
                if isinstance(item, dict)
                else item
                for item in val
            ]
        else:
            cleaned[key] = val
    return cleaned
