"""AI-HAS Agentic Orchestrator: Guardrails -> Hybrid RAG -> OpenAI Function Calling -> SSE Stream."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.agent.tools import (
    OPENAI_TOOLS,
    TIER_A_SOFTWARE,
    TIER_B_SOFTWARE,
    TIER_C_PROHIBITED,
    ToolContext,
    execute_tool_call,
)
from app.auth.rbac import AuthenticatedUser
from app.config import get_settings
from app.database import AuditLog, ChatMessage, ChatSession, MetricRecord, utcnow
from app.guardrails.injection import inspect_prompt_injection
from app.guardrails.pii import scrub_pii
from app.rag.retriever import CitationResult, HybridRetriever

logger = logging.getLogger("ai_has.orchestrator")

_WORD_RE = re.compile(r"[a-z0-9]{3,}")

SYSTEM_PROMPT = """You are AI-HAS, the Enterprise Level-1 IT Support Assistant for Acme Corp.
Follow these strict operating rules:
1. Ground every technical and policy answer strictly in the RETRIEVED SOP CONTEXT or TOOL EXECUTION RESULTS provided below. Never invent policies, timelines, IP addresses, or error codes.
2. Always include inline source citations such as [SOP-IT-001] or [SOP-IT-002] when referencing SOP instructions.
3. Execute operational workflows proactively via tools when the user asks to:
   - Reset their Okta SSO / Active Directory / VPN password -> call `trigger_password_reset`.
   - Request software access or licenses -> call `provision_software_access`.
   - Open an IT ticket (hardware failure, peripheral dispatch, email quarantine) -> call `create_it_ticket`.
   - Escalate complex/high-risk issues (lost/stolen laptop, SEC-LOCK, lost phone with no backup MFA, persistent VPN-412, aws-prod-admin, or explicit request for a human agent) -> call `escalate_to_human` with a concise structured `handoff_summary`.
4. Never accept, ask for, or repeat plaintext passwords, SSNs, or API secrets.
5. If a question is completely outside corporate IT support (e.g., cooking recipes, stock tips, personal homework) and unsupported by the SOPs, clearly state that it is out of scope for Enterprise IT Helpdesk without fabricating an answer, and offer to log a ticket if it relates to a work tool.
"""


def _format_sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _compute_rag_scores(
    intent: str,
    response_text: str,
    citations: List[CitationResult],
    tool_calls: List[Dict[str, Any]],
) -> Tuple[float, float]:
    """Compute bounded Faithfulness and Relevance scores [0.0, 1.0] for KPI observability."""
    if intent == "security_blocked":
        return 1.0, 1.0

    if citations:
        top_scores = [c.score for c in citations[:3]]
        relevance = round(min(0.99, max(0.05, sum(top_scores) / len(top_scores))), 3)
    else:
        relevance = 0.25

    if intent == "out_of_scope":
        # Refusing an out-of-scope query without hallucinating yields high faithfulness
        return 0.96, round(relevance, 3)

    context_corpus = " ".join(c.excerpt.lower() for c in citations)
    tool_corpus = json.dumps(tool_calls, default=str).lower()
    grounding_pool = set(_WORD_RE.findall(context_corpus + " " + tool_corpus))
    resp_tokens = _WORD_RE.findall(response_text.lower())

    if not resp_tokens:
        return 0.85, relevance

    grounded_hits = sum(1 for tok in resp_tokens if tok in grounding_pool)
    overlap = grounded_hits / max(len(resp_tokens), 1)
    # Tool-executed responses combine deterministic tool outputs + concise guidance
    base = 0.72 if tool_calls else 0.58
    faithfulness = round(min(0.99, max(0.50, base + 0.40 * overlap)), 3)
    if tool_calls and relevance < 0.75:
        relevance = round(max(relevance, 0.86), 3)
    return faithfulness, relevance


def _build_deterministic_handoff_summary(
    user: AuthenticatedUser,
    redacted_query: str,
    history: List[ChatMessage],
    citations: List[CitationResult],
) -> str:
    recent_user_msgs = [m.content for m in history if m.sender_role == "user"][-3:]
    sop_refs = ", ".join(sorted({c.doc_code for c in citations[:3]})) or "None"
    return (
        f"Requester: {user.full_name} ({user.email}, {user.department}). "
        f"Latest issue: {redacted_query[:260]}. "
        f"Prior user context count: {len(recent_user_msgs)}. "
        f"Referenced SOPs: {sop_refs}. Escalated for Tier-2 support inspection."
    )


def _detect_software_from_text(lower_text: str) -> Optional[str]:
    all_keys = list(TIER_C_PROHIBITED) + list(TIER_B_SOFTWARE.keys()) + list(TIER_A_SOFTWARE.keys())
    for key in all_keys:
        if key in lower_text:
            return key
    return None


def _detect_category_from_text(lower_text: str) -> str:
    if any(k in lower_text for k in ("vpn", "globalprotect", "gateway", "dns", "vpn-")):
        return "network_vpn"
    if any(k in lower_text for k in ("mfa", "okta verify", "yubikey", "authenticator", "sso-403", "sso-loop")):
        return "iam_mfa"
    if any(k in lower_text for k in ("password", "lockout", "sec-lock")):
        return "iam_password"
    if any(k in lower_text for k in ("laptop", "macbook", "charger", "screen", "stolen", "lost my laptop", "hardware", "monitor")):
        return "hardware"
    if any(k in lower_text for k in ("email", "outlook", "gmail", "550 5.7.1", "distribution list", "bounce")):
        return "email_messaging"
    if any(k in lower_text for k in ("access", "license", "provision", "figma", "github", "slack", "notion", "zoom", "jira")):
        return "software_access"
    return "general_it"


def _detect_priority_from_text(lower_text: str) -> str:
    if any(k in lower_text for k in ("stolen", "lost my laptop", "lost laptop", "sec-lock", "compromised", "outage", "urgent")):
        return "critical"
    if any(k in lower_text for k in ("unbootable", "locked out", "vpn-412", "blocked", "lost my phone")):
        return "high"
    return "medium"


async def _run_deterministic_fallback(
    redacted_query: str,
    citations: List[CitationResult],
    history: List[ChatMessage],
    ctx: ToolContext,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """Run grounded RAG + deterministic tool routing when OpenAI API key is omitted."""
    lower = redacted_query.lower()
    executed_tools: List[Dict[str, Any]] = []

    # 1. Explicit or policy-mandated escalation triggers
    explicit_escalation = any(
        phrase in lower
        for phrase in (
            "speak to a human",
            "human agent",
            "talk to an agent",
            "escalate",
            "live support",
            "transfer me",
            "stolen laptop",
            "lost my laptop",
            "lost laptop",
            "sec-lock",
            "lost my phone and",
            "no backup mfa",
            "persistent vpn-412",
            "crowdstrike is missing",
        )
    )
    if explicit_escalation:
        category = _detect_category_from_text(lower)
        priority = _detect_priority_from_text(lower)
        summary = _build_deterministic_handoff_summary(ctx.user, redacted_query, history, citations)
        res = await execute_tool_call(
            "escalate_to_human",
            {
                "reason": redacted_query[:220],
                "category": category,
                "priority": priority,
                "handoff_summary": summary,
            },
            ctx,
        )
        executed_tools.append({"tool_name": "escalate_to_human", "arguments": {"category": category, "priority": priority}, "result": res, "status": res.get("status")})
        cite_tag = f" [{citations[0].doc_code}]" if citations else ""
        reply = (
            f"I have escalated your request to a live **Tier-2 IT Support Agent**{cite_tag}.\n\n"
            f"- **Escalation Ticket:** `{res['ticket_id']}` (Priority: **{priority.upper()}**, Category: `{category}`)\n"
            f"- **AI Handoff Summary:** {summary}\n\n"
            "A support agent in the Human-in-the-Loop queue can take over this conversation immediately."
        )
        return "escalate", reply, executed_tools

    # 2. Password reset workflow
    if any(k in lower for k in ("reset my password", "reset password", "forgot my password", "password reset", "locked out of okta")):
        system = "vpn" if "vpn" in lower else "active_directory" if "active directory" in lower else "okta"
        target = ctx.extracted_emails[0] if ctx.extracted_emails else ctx.user.email
        args = {"employee_email": target, "system": system, "reason": redacted_query[:140]}
        res = await execute_tool_call("trigger_password_reset", args, ctx)
        executed_tools.append({"tool_name": "trigger_password_reset", "arguments": {"system": system}, "result": res, "status": res.get("status")})
        if res["status"] == "success":
            reply = (
                f"I have initiated a secure **{system.upper()} password reset** per **[SOP-IT-002 §4]**.\n\n"
                f"- **Target Account:** `{res['target_email']}`\n"
                f"- **Delivery Channel:** Secondary recovery email (single-use link expires in **{res['expires_in_minutes']} minutes**)\n"
                f"- **Workflow Reference:** `{res.get('external_ref') or res['webhook_id']}`\n\n"
                "After updating your password, remember to lock and unlock your workstation while on VPN so cached credentials sync."
            )
        elif res["status"] == "denied":
            reply = f"Password reset could not be automated: {res['reason']} **[SOP-IT-002 §4]**"
        else:
            reply = f"Automated reset requires human escalation: {res['reason']} **[SOP-IT-002 §5]**"
        return "tool_password_reset", reply, executed_tools

    # 3. Software access provisioning workflow
    software_hit = _detect_software_from_text(lower)
    if software_hit and any(
        k in lower for k in ("access", "provision", "license", "install", "seat", "need ", "request ")
    ):
        level = "admin" if "admin" in lower else "editor" if "editor" in lower else "viewer" if "viewer" in lower else "standard"
        args = {
            "software_name": software_hit,
            "access_level": level,
            "business_justification": redacted_query[:200],
        }
        res = await execute_tool_call("provision_software_access", args, ctx)
        executed_tools.append({"tool_name": "provision_software_access", "arguments": args, "result": res, "status": res.get("status")})
        if res["status"] == "provisioned":
            reply = (
                f"**{res['software']}** (`{level}`) has been **auto-provisioned** per **[SOP-IT-003 §2.1]**.\n\n"
                f"- **Tier:** {res['tier']}\n"
                f"- **Tracking Ticket:** `{res['ticket_id']}`\n"
                f"- **Availability:** {res['eta']}\n"
                f"- **SCIM Reference:** `{res.get('external_ref') or res['webhook_id']}`"
            )
        elif res["status"] == "pending_approval":
            reply = (
                f"Your request for **{res['software']}** (`{level}`) requires manager approval per **[SOP-IT-003 §2.2]**.\n\n"
                f"- **Tier:** {res['tier']}\n"
                f"- **Approval Ticket:** `{res['ticket_id']}` (SLA: **{res['sla']}**)\n"
                f"- **Webhook Reference:** `{res.get('external_ref') or res['webhook_id']}`"
            )
        else:
            reply = f"Request blocked per **[SOP-IT-003 §2.3]**: {res['reason']}"
        return "tool_software_access", reply, executed_tools

    # 4. Ticket logging workflow
    if any(k in lower for k in ("create a ticket", "open a ticket", "log a ticket", "file a ticket", "replacement laptop", "broken screen", "swelling battery")):
        cat = _detect_category_from_text(lower)
        prio = _detect_priority_from_text(lower)
        args = {
            "title": redacted_query[:120],
            "description": redacted_query,
            "category": cat,
            "priority": prio,
        }
        res = await execute_tool_call("create_it_ticket", args, ctx)
        executed_tools.append({"tool_name": "create_it_ticket", "arguments": args, "result": res, "status": res.get("status")})
        reply = (
            f"I have logged an IT support ticket for your request.\n\n"
            f"- **Ticket ID:** `{res['ticket_id']}`\n"
            f"- **Category / Priority:** `{cat}` / **{prio.upper()}**\n"
            f"- **External ITSM Reference:** `{res.get('external_ref') or res['webhook_id']}`"
        )
        return "tool_create_ticket", reply, executed_tools

    # 5. Out-of-scope check (low retrieval score + no IT domain indicators)
    it_domain_terms = (
        "vpn", "password", "mfa", "sso", "okta", "laptop", "email", "wifi", "software",
        "jira", "github", "figma", "slack", "zoom", "ticket", "hardware", "yubikey", "dns", "bitlocker",
    )
    top_score = citations[0].score if citations else 0.0
    if top_score < 0.38 and not any(term in lower for term in it_domain_terms):
        reply = (
            "I could not find any matching corporate IT Standard Operating Procedure for this request. "
            "As the **Acme Corp Level-1 IT Helpdesk Assistant**, I only answer IT support questions grounded in "
            "approved SOPs (VPN, SSO/MFA, Password Policy, Software Provisioning, Hardware & Corporate Email) "
            "or execute authorized IT workflows.\n\n"
            "If this involves an enterprise tool or workplace issue, please provide more IT context or ask me to **escalate to a human agent**."
        )
        return "out_of_scope", reply, executed_tools

    # 6. Grounded RAG QA synthesis directly from top retrieved SOP chunks
    if not citations:
        reply = (
            "No SOP guidance matched your query in the knowledge base. "
            "Would you like me to create an IT support ticket or escalate to a human agent?"
        )
        return "out_of_scope", reply, executed_tools

    top_cites = citations[:3]
    lines = ["Based on our corporate IT Standard Operating Procedures, here is the verified guidance:\n"]
    for idx, cite in enumerate(top_cites, start=1):
        lines.append(f"**{idx}. [{cite.doc_code}] {cite.section}** *(confidence {cite.score:.2f})*")
        lines.append(f"{cite.excerpt}\n")
    lines.append(
        "If these steps do not resolve the issue, say **\"escalate to human\"** and I will hand off a summary to a live Support Agent."
    )
    return "rag_qa", "\n".join(lines), executed_tools


_TOOL_INTENTS = (
    ("escalate_to_human", "escalate"),
    ("trigger_password_reset", "tool_password_reset"),
    ("provision_software_access", "tool_software_access"),
    ("create_it_ticket", "tool_create_ticket"),
)

_ACTION_LABELS = {
    "trigger_password_reset": "Password reset",
    "provision_software_access": "Software access request",
    "create_it_ticket": "IT ticket",
    "escalate_to_human": "Escalation to a human agent",
}


def _intent_from_tools(
    executed_tools: List[Dict[str, Any]], citations: List[CitationResult], final_text: str
) -> str:
    """Classify an LLM turn by the workflow tools it actually executed."""
    names = {t["tool_name"] for t in executed_tools}
    for tool_name, intent in _TOOL_INTENTS:
        if tool_name in names:
            return intent
    if not citations or (citations[0].score < 0.25 and "out of scope" in final_text.lower()):
        return "out_of_scope"
    return "rag_qa"


def _summarize_interrupted_actions(actions: List[Dict[str, Any]]) -> str:
    """Tell the user which real actions already ran before the LLM provider failed."""
    lines = ["The AI assistant was interrupted, but these actions were already carried out for your request:\n"]
    for t in actions:
        lines.append(f"- **{_ACTION_LABELS.get(t['tool_name'], t['tool_name'])}**: {t.get('status') or 'unknown'}")
    lines.append(
        "\nSee the action details above. Please don't repeat the request; if anything looks wrong, "
        "ask me to **escalate to a human agent**."
    )
    return "\n".join(lines)


async def _run_openai_orchestration(
    redacted_query: str,
    citations: List[CitationResult],
    history: List[ChatMessage],
    ctx: ToolContext,
    executed_tools: List[Dict[str, Any]],
) -> Tuple[str, str, List[Dict[str, Any]], int]:
    """Execute full OpenAI gpt-4o-mini function-calling loop grounded in retrieved SOP chunks.

    Executed tools are appended to the caller's `executed_tools` list as they run, so the
    caller still knows about real side effects if a later provider call raises.
    """
    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    retriever = HybridRetriever()
    context_block = retriever.format_context(citations)

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n### RETRIEVED SOP CONTEXT ###\n{context_block}",
        }
    ]
    for msg in history[-6:]:
        role = "assistant" if msg.sender_role in {"assistant", "support_agent"} else "user"
        messages.append({"role": role, "content": msg.content})
    messages.append({"role": "user", "content": redacted_query})

    total_tokens = 0
    final_text = ""

    for _round in range(4):
        resp = await client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=messages,
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )
        if resp.usage:
            total_tokens += resp.usage.total_tokens or 0

        choice = resp.choices[0].message
        if choice.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.tool_calls
                    ],
                }
            )
            for tc in choice.tool_calls:
                try:
                    parsed_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    parsed_args = {}
                tool_res = await execute_tool_call(tc.function.name, parsed_args, ctx)
                executed_tools.append(
                    {
                        "tool_name": tc.function.name,
                        "arguments": parsed_args,
                        "result": tool_res,
                        "status": tool_res.get("status"),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_res, default=str),
                    }
                )
            continue

        final_text = (choice.content or "").strip()
        break

    if not final_text:
        final_text = "Your IT request has been processed. Please review the action details above."

    intent = _intent_from_tools(executed_tools, citations, final_text)
    return intent, final_text, executed_tools, total_tokens


async def _run_gemini_orchestration(
    redacted_query: str,
    citations: List[CitationResult],
    history: List[ChatMessage],
    ctx: ToolContext,
    executed_tools: List[Dict[str, Any]],
) -> Tuple[str, str, List[Dict[str, Any]], int]:
    """Run Gemini function calling while reusing the guarded project tool handlers.

    Like the OpenAI runner, executed tools are appended to the caller's list as they run.
    """
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    retriever = HybridRetriever()
    contents = []
    for msg in history[-6:]:
        contents.append(types.Content(
            role="model" if msg.sender_role in {"assistant", "support_agent"} else "user",
            parts=[types.Part(text=msg.content)],
        ))
    contents.append(types.Content(role="user", parts=[types.Part(text=redacted_query)]))
    tools = [{
        "function_declarations": [
            {"name": item["function"]["name"],
             "description": item["function"]["description"],
             "parameters": item["function"]["parameters"]}
            for item in OPENAI_TOOLS
        ]
    }]
    config = types.GenerateContentConfig(
        system_instruction=f"{SYSTEM_PROMPT}\n\n### RETRIEVED SOP CONTEXT ###\n{retriever.format_context(citations)}",
        tools=tools,
        temperature=0.1,
    )
    total_tokens = 0
    final_text = ""

    for _round in range(4):
        response = await client.aio.models.generate_content(
            model=settings.gemini_chat_model, contents=contents, config=config
        )
        usage = getattr(response, "usage_metadata", None)
        if usage:
            total_tokens += getattr(usage, "total_token_count", 0) or 0
        calls = list(getattr(response, "function_calls", None) or [])
        if not calls:
            final_text = (getattr(response, "text", None) or "").strip()
            break
        candidate_content = response.candidates[0].content
        contents.append(candidate_content)
        results = []
        for call in calls:
            args = dict(call.args or {})
            result = await execute_tool_call(call.name, args, ctx)
            executed_tools.append({"tool_name": call.name, "arguments": args,
                                  "result": result, "status": result.get("status")})
            results.append(types.Part.from_function_response(
                name=call.name, response={"result": result}
            ))
        contents.append(types.Content(role="user", parts=results))

    if not final_text:
        final_text = "Your IT request has been processed. Please review the action details above."
    intent = _intent_from_tools(executed_tools, citations, final_text)
    return intent, final_text, executed_tools, total_tokens


def _get_or_create_session(
    db: Session,
    user: AuthenticatedUser,
    session_id: Optional[str],
    initial_title: str,
) -> ChatSession:
    if session_id:
        existing = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if existing:
            return existing

    clean_title = (initial_title or "New IT Support Request").strip()[:80]
    session = ChatSession(
        user_id=user.id,
        user_email=user.email,
        title=clean_title or "New IT Support Request",
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


async def stream_chat_turn(
    db: Session,
    user: AuthenticatedUser,
    message: str,
    session_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Execute full guarded AI-HAS turn and yield Server-Sent Events."""
    started = time.perf_counter()
    settings = get_settings()

    # 1. Prompt Injection Check BEFORE touching PII/LLM
    injection_res = inspect_prompt_injection(message)
    pii_res = scrub_pii(message)
    safe_user_content = pii_res.redacted_text

    session = _get_or_create_session(
        db=db,
        user=user,
        session_id=session_id,
        initial_title=safe_user_content[:60],
    )

    yield _format_sse(
        "session",
        {"session_id": session.id, "status": session.status, "title": session.title},
    )

    # Persist user message (redacted)
    user_msg = ChatMessage(
        session_id=session.id,
        sender_role="user",
        sender_email=user.email,
        content=safe_user_content,
        pii_redacted=pii_res.was_redacted,
    )
    db.add(user_msg)
    session.updated_at = utcnow()
    db.commit()

    if pii_res.was_redacted:
        db.add(
            AuditLog(
                event_type="pii_redacted",
                severity="info",
                actor_email=user.email,
                actor_role=user.role,
                session_id=session.id,
                action=f"Scrubbed {pii_res.findings_count} PII/secret item(s) before LLM processing",
                details_json={
                    "categories": pii_res.categories,
                    "findings_count": pii_res.findings_count,
                },
            )
        )
        db.commit()

    yield _format_sse(
        "guardrail",
        {
            "pii_redacted": pii_res.was_redacted,
            "pii_categories": pii_res.categories,
            "injection_blocked": injection_res.is_blocked,
            "risk_score": injection_res.risk_score,
            "matched_rules": injection_res.matched_rules,
        },
    )

    # 2. If human agent has taken over, route message directly to human thread
    if session.status == "agent_active":
        notice = (
            f"Your message has been delivered directly to Support Agent "
            f"**{session.assigned_agent_email or 'Tier-2 IT Support'}**."
        )
        sys_msg = ChatMessage(
            session_id=session.id,
            sender_role="system",
            content=notice,
            intent="human_takeover_active",
        )
        db.add(sys_msg)
        db.commit()
        yield _format_sse("token", {"delta": notice})
        yield _format_sse(
            "done",
            {
                "message_id": sys_msg.id,
                "session_id": session.id,
                "session_status": session.status,
                "intent": "human_takeover_active",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "citations": [],
                "tool_calls": [],
                "deflected": False,
                "llm_mode": "human_agent",
            },
        )
        return

    # 3. Block prompt injections immediately
    if injection_res.is_blocked:
        db.add(
            AuditLog(
                event_type="injection_blocked",
                severity="high",
                actor_email=user.email,
                actor_role=user.role,
                session_id=session.id,
                action="Blocked suspected prompt injection or jailbreak attempt",
                details_json={
                    "risk_score": injection_res.risk_score,
                    "matched_rules": injection_res.matched_rules,
                    "reason": injection_res.reason,
                },
            )
        )
        blocked_text = (
            "Security Policy Alert: Your request was blocked by the AI-HAS Prompt Injection Guardrail "
            f"({', '.join(injection_res.matched_rules)}). Please rephrase your legitimate IT support question."
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
        assistant_msg = ChatMessage(
            session_id=session.id,
            sender_role="assistant",
            content=blocked_text,
            intent="security_blocked",
            latency_ms=latency_ms,
        )
        db.add(assistant_msg)
        db.add(
            MetricRecord(
                session_id=session.id,
                metric_type="chat_turn",
                intent="security_blocked",
                latency_ms=latency_ms,
                # A blocked attack is neither a resolved request nor a graded answer;
                # keep it out of the deflection rate and the RAG quality averages.
                faithfulness_score=None,
                relevance_score=None,
                deflected=False,
                escalated=False,
            )
        )
        db.commit()
        yield _format_sse("token", {"delta": blocked_text})
        yield _format_sse(
            "done",
            {
                "message_id": assistant_msg.id,
                "session_id": session.id,
                "session_status": session.status,
                "intent": "security_blocked",
                "latency_ms": latency_ms,
                "faithfulness_score": None,
                "relevance_score": None,
                "deflected": False,
                "citations": [],
                "tool_calls": [],
                "llm_mode": "guardrail",
            },
        )
        return

    # 4. Hybrid RAG Retrieval
    retriever = HybridRetriever()
    citations = retriever.retrieve(safe_user_content, top_k=settings.rag_top_k)
    citations_payload = [c.to_dict() for c in citations]
    yield _format_sse("citations", {"citations": citations_payload})

    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id, ChatMessage.id != user_msg.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    ctx = ToolContext(
        db=db,
        user=user,
        session_id=session.id,
        extracted_emails=pii_res.extracted_emails,
    )

    # 5. Orchestrate with selected provider or deterministic local fallback
    tokens_used = 0
    llm_mode = "deterministic"
    if settings.active_llm_provider in {"openai", "gemini"}:
        executed: List[Dict[str, Any]] = []
        try:
            runner = _run_gemini_orchestration if settings.active_llm_provider == "gemini" else _run_openai_orchestration
            intent, response_text, tool_calls, tokens_used = await runner(
                redacted_query=safe_user_content,
                citations=citations,
                history=history,
                ctx=ctx,
                executed_tools=executed,
            )
            llm_mode = settings.active_llm_provider
        except Exception:
            logger.exception(
                "%s orchestration failed; serving deterministic fallback [session_id=%s]",
                settings.active_llm_provider,
                session.id,
            )
            # Workflow tools may already have run (a real reset, ticket or escalation).
            # Report them instead of hiding them, and don't re-run them via the fallback.
            actions = [t for t in executed if t["tool_name"] != "search_it_knowledge_base"]
            if actions:
                tool_calls = executed
                intent = _intent_from_tools(executed, citations, "")
                response_text = _summarize_interrupted_actions(actions)
            else:
                intent, response_text, tool_calls = await _run_deterministic_fallback(
                    redacted_query=safe_user_content,
                    citations=citations,
                    history=history,
                    ctx=ctx,
                )
            llm_mode = "deterministic_fallback"
    else:
        intent, response_text, tool_calls = await _run_deterministic_fallback(
            redacted_query=safe_user_content,
            citations=citations,
            history=history,
            ctx=ctx,
        )

    for tc in tool_calls:
        yield _format_sse("tool_result", tc)

    # Stream response text progressively in natural chunks for responsive SSE UX
    words = response_text.split(" ")
    chunk_size = 6
    for i in range(0, len(words), chunk_size):
        piece = " ".join(words[i : i + chunk_size])
        if i + chunk_size < len(words):
            piece += " "
        yield _format_sse("token", {"delta": piece})
        await asyncio.sleep(0.01)

    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    faithfulness, relevance = _compute_rag_scores(intent, response_text, citations, tool_calls)

    db.refresh(session)
    escalated = session.status == "escalated" or intent == "escalate"
    deflected = not escalated and intent != "out_of_scope"
    if not escalated and session.status == "active" and deflected:
        session.deflected = True

    assistant_msg = ChatMessage(
        session_id=session.id,
        sender_role="assistant",
        content=response_text,
        citations_json=citations_payload,
        tool_calls_json=tool_calls,
        intent=intent,
        latency_ms=latency_ms,
    )
    db.add(assistant_msg)
    db.add(
        MetricRecord(
            session_id=session.id,
            metric_type="chat_turn",
            intent=intent,
            latency_ms=latency_ms,
            faithfulness_score=faithfulness,
            relevance_score=relevance,
            deflected=deflected,
            escalated=escalated,
            tokens_used=tokens_used or None,
        )
    )
    db.commit()
    db.refresh(assistant_msg)

    yield _format_sse(
        "done",
        {
            "message_id": assistant_msg.id,
            "session_id": session.id,
            "session_status": session.status,
            "intent": intent,
            "latency_ms": latency_ms,
            "faithfulness_score": faithfulness,
            "relevance_score": relevance,
            "deflected": deflected,
            "citations": citations_payload,
            "tool_calls": tool_calls,
            "llm_mode": llm_mode,
        },
    )


async def run_chat_turn(
    db: Session,
    user: AuthenticatedUser,
    message: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a complete chat turn synchronously and return the final structured payload."""
    final_payload: Dict[str, Any] = {}
    guardrail_info: Dict[str, Any] = {}
    tokens: List[str] = []

    async for raw_sse in stream_chat_turn(db=db, user=user, message=message, session_id=session_id):
        lines = raw_sse.strip().splitlines()
        ev_name = ""
        ev_data = {}
        for line in lines:
            if line.startswith("event: "):
                ev_name = line[7:].strip()
            elif line.startswith("data: "):
                ev_data = json.loads(line[6:])
        if ev_name == "guardrail":
            guardrail_info = ev_data
        elif ev_name == "token":
            tokens.append(ev_data.get("delta", ""))
        elif ev_name == "done":
            final_payload = ev_data

    final_payload["reply"] = "".join(tokens)
    final_payload["guardrail"] = guardrail_info
    return final_payload
