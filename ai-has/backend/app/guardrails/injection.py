"""Prompt-injection, jailbreak, and policy-evasion detector."""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class InjectionCheckResult:
    """Result from prompt-injection and jailbreak inspection."""

    is_blocked: bool
    risk_score: float
    matched_rules: List[str] = field(default_factory=list)
    reason: str = ""


_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_BASE64_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")

# (rule_id, weight, compiled_pattern, explanation)
_INJECTION_RULES: List[Tuple[str, float, re.Pattern[str], str]] = [
    (
        "instruction_override",
        0.75,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(?:previous|prior|above|all|system|security|guardrail|safety|policy)\b.{0,30}\b(?:instructions?|rules?|prompts?|policies|constraints?|filters?)?",
            re.IGNORECASE,
        ),
        "Attempted instruction or safety guardrail override",
    ),
    (
        "jailbreak_persona",
        0.80,
        re.compile(
            r"\b(?:you are now|act as|switch to|enter|enable)\b.{0,35}\b(?:DAN|do anything now|developer mode|unrestricted|jailbreak|evil|unfiltered|god mode|root admin)\b",
            re.IGNORECASE,
        ),
        "Attempted jailbreak or unrestricted persona adoption",
    ),
    (
        "system_prompt_exfiltration",
        0.85,
        re.compile(
            r"\b(?:reveal|show|print|dump|output|repeat|leak|expose|display)\b.{0,40}\b(?:system prompt|hidden instructions|developer message|internal instructions|openai_api_key|api[_\s]?key|jwt[_\s]?secret|environment variables|\.env)\b",
            re.IGNORECASE,
        ),
        "Attempted exfiltration of system prompt, secrets, or environment credentials",
    ),
    (
        "bulk_data_exfiltration",
        0.70,
        re.compile(
            r"\b(?:dump|export|select \*|print all|list all|extract all)\b.{0,40}\b(?:users|employees|passwords|ssn|social security|audit_logs|database|tokens|secrets)\b",
            re.IGNORECASE,
        ),
        "Attempted unauthorized bulk data or credential extraction",
    ),
    (
        "delimiter_spoofing",
        0.85,
        re.compile(
            r"(?:<\|im_start\|>\s*system|<\|system\|>|\[INST\]\s*<<SYS>>|###\s*(?:SYSTEM|DEVELOPER)\s*(?:OVERRIDE|INSTRUCTION))",
            re.IGNORECASE,
        ),
        "Detected LLM chat-template delimiter spoofing",
    ),
    (
        "privilege_escalation",
        0.75,
        re.compile(
            r"\b(?:elevate|grant|promote|set)\b.{0,30}\b(?:my role|role|permissions?|access)\b.{0,25}\b(?:admin|administrator|domain admin|superuser|root)\b",
            re.IGNORECASE,
        ),
        "Attempted prompt-driven RBAC privilege escalation",
    ),
    (
        "sql_or_command_injection",
        0.70,
        re.compile(
            r"(?:;\s*DROP\s+TABLE\b|UNION\s+SELECT\b|\$\([^)]*(?:cat\s+/etc|env|printenv|curl|wget)[^)]*\))",
            re.IGNORECASE,
        ),
        "Detected SQL or command injection payload",
    ),
]


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    return normalized


def _decode_suspicious_base64(text: str) -> str:
    decoded_chunks: List[str] = []
    for candidate in _BASE64_CANDIDATE_RE.findall(text):
        try:
            raw = base64.b64decode(candidate, validate=True)
            decoded = raw.decode("utf-8", errors="ignore")
            if decoded and sum(ch.isprintable() for ch in decoded) / max(len(decoded), 1) > 0.85:
                decoded_chunks.append(decoded)
        except Exception:
            continue
    return " ".join(decoded_chunks)


def inspect_prompt_injection(text: str, threshold: float = 0.65) -> InjectionCheckResult:
    """Inspect user input (plus normalized & decoded fragments) for prompt injection."""
    if not text or not text.strip():
        return InjectionCheckResult(is_blocked=False, risk_score=0.0)

    normalized = _normalize_text(text)
    decoded_b64 = _decode_suspicious_base64(normalized)
    combined_surface = f"{normalized}\n{decoded_b64}".strip()

    matched_rules: List[str] = []
    reasons: List[str] = []
    score = 0.0

    for rule_id, weight, pattern, explanation in _INJECTION_RULES:
        if pattern.search(combined_surface):
            matched_rules.append(rule_id)
            reasons.append(explanation)
            score = min(1.0, score + weight)

    if decoded_b64 and matched_rules:
        # Obfuscation amplifies risk
        score = min(1.0, score + 0.15)
        if "obfuscated_base64" not in matched_rules:
            matched_rules.append("obfuscated_base64")

    is_blocked = score >= threshold
    reason = "; ".join(reasons) if reasons else ""
    return InjectionCheckResult(
        is_blocked=is_blocked,
        risk_score=round(score, 3),
        matched_rules=matched_rules,
        reason=reason,
    )
