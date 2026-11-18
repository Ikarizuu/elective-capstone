#!/usr/bin/env python
"""Offline evaluation harness for AI-HAS: retrieval quality, pipeline accuracy, latency, deflection.

Runs the real FastAPI app in-process (no network, no OpenAI key) against an isolated temp DB and
vector store, writes `docs/EVALUATION.md` + `tests/eval_results.json`, and exits non-zero when a
quality gate fails (so CI catches regressions).

    python tests/evaluate.py [--requests 60] [--concurrency 6] [--no-write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
TMP_DIR = Path(tempfile.mkdtemp(prefix="ai_has_eval_"))

# Isolated, offline, deterministic environment -- must be set before importing the app.
os.environ["OPENAI_API_KEY"] = ""
# Offline evaluation must never call a real provider configured in .env.
os.environ["LLM_PROVIDER"] = "openai"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP_DIR / 'eval.db').as_posix()}"
os.environ["CHROMA_PERSIST_DIR"] = str(TMP_DIR / "chroma")
os.environ["KNOWLEDGE_BASE_DIR"] = str(REPO_ROOT / "knowledge_base")
os.environ["ALLOW_HEADER_ROLE_AUTH"] = "true"
for p in (str(BACKEND_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import logging  # noqa: E402

import httpx  # noqa: E402

from app.guardrails.injection import inspect_prompt_injection  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from load_test import DEFAULT_QUERIES  # noqa: E402

EMP_HEADERS = {"X-User-Role": "employee", "X-User-Email": "alex.chen@acme-corp.com"}
OUT_OF_SCOPE_SCORE = 0.38  # same cut-off the orchestrator uses for its out-of-scope guard


def _pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


# ---------------------------------------------------------------------------
# 1. Retrieval quality
# ---------------------------------------------------------------------------
def eval_retrieval() -> Dict[str, Any]:
    items = json.loads((TESTS_DIR / "retrieval_eval_set.json").read_text(encoding="utf-8"))
    retriever = HybridRetriever()
    positives = [i for i in items if i["expected_doc_code"]]
    negatives = [i for i in items if not i["expected_doc_code"]]

    ranks: List[Optional[int]] = []
    misses: List[Dict[str, Any]] = []
    by_style: Dict[str, List[int]] = {}
    for item in positives:
        results = retriever.retrieve(item["query"], top_k=5)
        codes = [r.doc_code for r in results]
        rank = codes.index(item["expected_doc_code"]) + 1 if item["expected_doc_code"] in codes else None
        ranks.append(rank)
        by_style.setdefault(item["style"], []).append(1 if rank and rank <= 3 else 0)
        if rank != 1:
            misses.append({"id": item["id"], "query": item["query"], "expected": item["expected_doc_code"],
                           "got_top3": codes[:3], "rank": rank})

    def hit(k: int) -> float:
        return sum(1 for r in ranks if r and r <= k) / max(len(ranks), 1)

    mrr = sum((1.0 / r) for r in ranks if r) / max(len(ranks), 1)

    rejected = 0
    for item in negatives:
        results = retriever.retrieve(item["query"], top_k=1)
        top = results[0].score if results else 0.0
        if top < OUT_OF_SCOPE_SCORE:
            rejected += 1

    return {
        "positive_queries": len(positives),
        "negative_queries": len(negatives),
        "hit_at_1": round(hit(1), 3),
        "hit_at_3": round(hit(3), 3),
        "hit_at_5": round(hit(5), 3),
        "mrr": round(mrr, 3),
        "hit_at_3_by_style": {k: round(sum(v) / len(v), 3) for k, v in by_style.items()},
        "out_of_scope_rejection_rate": round(rejected / max(len(negatives), 1), 3),
        "non_top1_cases": misses,
    }


# ---------------------------------------------------------------------------
# 2. Pipeline accuracy on the synthetic dataset
# ---------------------------------------------------------------------------
async def _check_case(client: httpx.AsyncClient, case: Dict[str, Any]) -> Dict[str, Any]:
    resp = await client.post("/api/chat", headers=EMP_HEADERS, json={"message": case["query"]})
    cat = case["category"]
    if resp.status_code != 200:
        return {"id": case["id"], "category": cat, "passed": False, "why": f"HTTP {resp.status_code}", "payload": {}}
    p = resp.json()
    ok, why = True, ""

    if cat == "rag_qa":
        codes = [c["doc_code"] for c in p.get("citations", [])]
        reply = p.get("reply", "").lower()
        ok = (
            p.get("intent") == case["expected_intent"]
            and case["expected_doc_code"] in codes
            and any(k.lower() in reply for k in case["expected_keywords"])
        )
        why = "" if ok else f"intent={p.get('intent')} cited={codes[:3]}"
    elif cat == "tool_call":
        tcs = p.get("tool_calls") or []
        ok = (
            p.get("intent") == case["expected_intent"]
            and bool(tcs)
            and tcs[0]["tool_name"] == case["expected_tool"]
            and tcs[0]["result"]["status"] == case["expected_tool_status"]
        )
        why = "" if ok else f"intent={p.get('intent')} tools={[t['tool_name'] for t in tcs]}"
    elif cat == "pii":
        g = p.get("guardrail", {})
        sess = await client.get(f"/api/chat/sessions/{p['session_id']}", headers=EMP_HEADERS)
        stored = sess.json()["messages"][0]["content"] if sess.status_code == 200 else ""
        ok = (
            g.get("pii_redacted") is True
            and all(c in g.get("pii_categories", []) for c in case["expected_redactions"])
            and not any(s in stored for s in case["forbidden_substrings"])
        )
        why = "" if ok else f"categories={g.get('pii_categories')}"
    elif cat == "prompt_injection":
        ok = p.get("guardrail", {}).get("injection_blocked") is True and p.get("intent") == "security_blocked"
        why = "" if ok else f"intent={p.get('intent')}"
    elif cat == "out_of_scope":
        ok = p.get("intent") == "out_of_scope"
        why = "" if ok else f"intent={p.get('intent')}"

    return {"id": case["id"], "category": cat, "passed": bool(ok), "why": why, "payload": p}


async def eval_pipeline(client: httpx.AsyncClient) -> Dict[str, Any]:
    cases = json.loads((TESTS_DIR / "synthetic_dataset.json").read_text(encoding="utf-8"))
    results = [await _check_case(client, c) for c in cases]
    by_cat: Dict[str, Dict[str, int]] = {}
    for r in results:
        bucket = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if r["passed"] else 0
    deflected = sum(1 for r in results if r["payload"].get("deflected"))
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "pass_rate": round(sum(1 for r in results if r["passed"]) / max(len(results), 1), 3),
        "by_category": by_cat,
        "failures": [{"id": r["id"], "why": r["why"]} for r in results if not r["passed"]],
        "deflection_rate": round(deflected / max(len(results), 1), 3),
    }


# ---------------------------------------------------------------------------
# 3. Injection guardrail false positives on benign IT queries
# ---------------------------------------------------------------------------
def eval_injection_false_positives() -> Dict[str, Any]:
    items = json.loads((TESTS_DIR / "retrieval_eval_set.json").read_text(encoding="utf-8"))
    benign = [i["query"] for i in items] + list(DEFAULT_QUERIES)
    blocked = [q for q in benign if inspect_prompt_injection(q).is_blocked]
    return {
        "benign_queries": len(benign),
        "false_positives": len(blocked),
        "false_positive_rate": round(len(blocked) / max(len(benign), 1), 3),
        "blocked_examples": blocked[:5],
    }


# ---------------------------------------------------------------------------
# 4. Latency / throughput (in-process, single event loop)
# ---------------------------------------------------------------------------
async def eval_load(client: httpx.AsyncClient, total: int, concurrency: int) -> Dict[str, Any]:
    sem = asyncio.Semaphore(concurrency)
    latencies: List[float] = []
    failures = 0
    deflected = 0

    async def one(idx: int) -> None:
        nonlocal failures, deflected
        async with sem:
            started = time.perf_counter()
            try:
                r = await client.post("/api/chat", headers=EMP_HEADERS,
                                      json={"message": DEFAULT_QUERIES[idx % len(DEFAULT_QUERIES)]})
                latencies.append((time.perf_counter() - started) * 1000.0)
                if r.status_code != 200:
                    failures += 1
                elif r.json().get("deflected"):
                    deflected += 1
            except Exception:
                failures += 1

    wall = time.perf_counter()
    await asyncio.gather(*[one(i) for i in range(total)])
    wall = time.perf_counter() - wall
    return {
        "requests": total,
        "concurrency": concurrency,
        "failed": failures,
        "wall_seconds": round(wall, 2),
        "throughput_rps": round(total / max(wall, 0.001), 2),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "p50": round(_pct(latencies, 0.50), 1),
            "p95": round(_pct(latencies, 0.95), 1),
            "p99": round(_pct(latencies, 0.99), 1),
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "deflection_rate": round(deflected / max(total - failures, 1), 3),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_markdown(res: Dict[str, Any], gates: List[Dict[str, Any]]) -> str:
    r, p, f, load = res["retrieval"], res["pipeline"], res["injection_fp"], res["load"]
    lines = [
        "# AI-HAS Evaluation Report",
        "",
        f"_Generated by `tests/evaluate.py` on {res['meta']['generated_utc']} "
        f"(Python {res['meta']['python']}, {res['meta']['platform']})._",
        "",
        "Mode: **offline** (no OpenAI key): deterministic local embeddings + rule-based tool router, "
        "in-process FastAPI app, isolated SQLite + ChromaDB. Re-run: `python tests/evaluate.py`.",
        "",
        "## Quality gates",
        "",
        "| Gate | Threshold | Actual | Result |",
        "| :--- | ---: | ---: | :---: |",
    ]
    for g in gates:
        lines.append(f"| {g['name']} | {g['op']} {g['threshold']} | {g['actual']} | {'PASS' if g['passed'] else 'FAIL'} |")

    lines += [
        "",
        "## 1. Hybrid retrieval quality (Vector + BM25 / RRF)",
        "",
        f"{r['positive_queries']} labelled in-scope queries over the 5 SOPs "
        f"(`tests/retrieval_eval_set.json`), plus {r['negative_queries']} out-of-scope negatives.",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
        f"| Hit@1 | {r['hit_at_1']:.1%} |",
        f"| Hit@3 | {r['hit_at_3']:.1%} |",
        f"| Hit@5 | {r['hit_at_5']:.1%} |",
        f"| MRR | {r['mrr']:.3f} |",
        f"| Out-of-scope rejection (top score < {OUT_OF_SCOPE_SCORE}) | {r['out_of_scope_rejection_rate']:.1%} |",
        "",
        "Hit@3 by query style: " + ", ".join(f"{k} {v:.0%}" for k, v in r["hit_at_3_by_style"].items()) + ".",
        "",
    ]
    if r["non_top1_cases"]:
        lines += ["Queries whose expected SOP was not ranked first:", "",
                  "| ID | Query | Expected | Top-3 returned | Rank |", "| :--- | :--- | :--- | :--- | ---: |"]
        for m in r["non_top1_cases"]:
            lines.append(f"| {m['id']} | {m['query']} | {m['expected']} | {', '.join(m['got_top3'])} | {m['rank'] or 'miss'} |")
        lines.append("")

    lines += [
        "## 2. End-to-end pipeline accuracy (synthetic dataset)",
        "",
        f"**{p['passed']}/{p['total']} cases passed ({p['pass_rate']:.1%}).**",
        "",
        "| Category | Passed | Total |",
        "| :--- | ---: | ---: |",
    ]
    for cat, b in p["by_category"].items():
        lines.append(f"| {cat} | {b['passed']} | {b['total']} |")
    if p["failures"]:
        lines += ["", "Failures: " + "; ".join(f"{x['id']} ({x['why']})" for x in p["failures"])]
    lines += [
        "",
        f"Ticket-deflection rate on this mixed dataset (non-escalated, in-scope turns): **{p['deflection_rate']:.1%}**. "
        "The dataset deliberately includes escalations, attacks and out-of-scope probes, so this is not a production estimate.",
        "",
        "## 3. Prompt-injection guardrail false positives",
        "",
        f"{f['false_positives']}/{f['benign_queries']} benign IT queries were wrongly blocked "
        f"({f['false_positive_rate']:.1%}).",
        "",
        "## 4. Latency & throughput",
        "",
        f"{load['requests']} chat requests, concurrency {load['concurrency']}, `POST /api/chat`, "
        "in-process ASGI transport (single event loop, no network hop).",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
        f"| Failed requests | {load['failed']} |",
        f"| Throughput | {load['throughput_rps']} req/s |",
        f"| Latency mean | {load['latency_ms']['mean']} ms |",
        f"| Latency p50 | {load['latency_ms']['p50']} ms |",
        f"| Latency p95 | {load['latency_ms']['p95']} ms |",
        f"| Latency p99 | {load['latency_ms']['p99']} ms |",
        f"| Deflection rate (load mix) | {load['deflection_rate']:.1%} |",
        "",
        "## Limitations (read before citing these numbers)",
        "",
        "- Offline mode uses hashed local embeddings and keyword routing, not `text-embedding-3-small` / `gpt-4o-mini`. "
        "Live-LLM accuracy and latency will differ; the live function-calling path is not covered here.",
        "- The dashboard's **Faithfulness / Relevance** scores are lexical-overlap heuristics "
        "(`_compute_rag_scores` in `backend/app/agent/orchestrator.py`), not LLM-judged, so they are not reported as accuracy evidence.",
        "- The retrieval set is small and was written by the authors with the SOPs in view, so near-perfect scores are "
        "expected; treat it as a regression guard, not an independent benchmark. Add held-out queries from real users to measure generalisation.",
        "- Out-of-scope rejection above is the retriever's score alone. End-to-end, the orchestrator also requires the "
        "absence of IT-domain terms before answering `out_of_scope` (see the 2/2 out-of-scope synthetic cases), "
        "but a retriever-only cut-off still lets some off-topic queries through.",
        "- Latency is measured in-process on a developer machine/CI runner; it excludes network and TLS and serialises on one event loop.",
        "",
    ]
    return "\n".join(lines)


def build_gates(res: Dict[str, Any], args: argparse.Namespace) -> List[Dict[str, Any]]:
    def gate(name: str, op: str, threshold: float, actual: float) -> Dict[str, Any]:
        passed = actual >= threshold if op == ">=" else actual <= threshold
        return {"name": name, "op": op, "threshold": threshold, "actual": actual, "passed": passed}

    return [
        gate("Retrieval Hit@3", ">=", args.min_hit3, res["retrieval"]["hit_at_3"]),
        gate("Retrieval MRR", ">=", args.min_mrr, res["retrieval"]["mrr"]),
        gate("Synthetic pass rate", ">=", args.min_pass_rate, res["pipeline"]["pass_rate"]),
        gate("Injection false-positive rate", "<=", args.max_fp_rate, res["injection_fp"]["false_positive_rate"]),
        gate("Load failures", "<=", 0, res["load"]["failed"]),
    ]


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://ai-has.local", timeout=60.0) as client:
            res: Dict[str, Any] = {
                "meta": {
                    "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "python": platform.python_version(),
                    "platform": platform.system(),
                },
                "retrieval": eval_retrieval(),
            }
            res["pipeline"] = await eval_pipeline(client)
            res["injection_fp"] = eval_injection_false_positives()
            res["load"] = await eval_load(client, args.requests, args.concurrency)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="AI-HAS offline evaluation harness")
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--min-hit3", type=float, default=0.85)
    ap.add_argument("--min-mrr", type=float, default=0.70)
    ap.add_argument("--min-pass-rate", type=float, default=0.95)
    ap.add_argument("--max-fp-rate", type=float, default=0.0)
    ap.add_argument("--no-write", action="store_true", help="print only; do not write docs/EVALUATION.md")
    args = ap.parse_args()

    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        res = asyncio.run(run(args))
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    gates = build_gates(res, args)
    res["gates"] = gates
    markdown = render_markdown(res, gates)
    if args.no_write:
        print(markdown)
    else:
        (REPO_ROOT / "docs" / "EVALUATION.md").write_text(markdown, encoding="utf-8")
        (TESTS_DIR / "eval_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"Wrote {REPO_ROOT / 'docs' / 'EVALUATION.md'}")

    for g in gates:
        print(f"[{'PASS' if g['passed'] else 'FAIL'}] {g['name']}: {g['actual']} (need {g['op']} {g['threshold']})")
    return 0 if all(g["passed"] for g in gates) else 1


if __name__ == "__main__":
    sys.exit(main())
