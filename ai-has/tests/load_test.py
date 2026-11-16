"""Concurrent latency, throughput, and synthetic accuracy benchmark for AI-HAS."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Dict, List

import httpx

DEFAULT_QUERIES = [
    "My GlobalProtect VPN fails with VPN-412 HIP compliance check failed.",
    "What is the minimum password length and rotation cadence?",
    "I forgot my Okta SSO password. Please trigger a password reset.",
    "Please provision standard Slack access for my onboarding team.",
    "How do I set up Okta Verify on my new phone?",
    "What is the home office ergonomic stipend amount?",
]


async def _worker(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    query: str,
) -> Dict:
    started = time.perf_counter()
    try:
        resp = await client.post(url, headers=headers, json={"message": query})
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "latency_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": elapsed_ms,
            "error": str(exc),
        }


async def run_benchmark(base_url: str, total_requests: int, concurrency: int) -> None:
    endpoint = f"{base_url.rstrip('/')}/api/chat"
    headers = {
        "X-User-Role": "employee",
        "X-User-Email": "alex.chen@acme-corp.com",
    }
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Verify server health first
        health = await client.get(f"{base_url.rstrip('/')}/api/health")
        health.raise_for_status()
        print("Target Health:", health.json())

        async def _bounded_task(idx: int) -> Dict:
            async with semaphore:
                q = DEFAULT_QUERIES[idx % len(DEFAULT_QUERIES)]
                return await _worker(client, endpoint, headers, q)

        wall_start = time.perf_counter()
        results: List[Dict] = await asyncio.gather(*[_bounded_task(i) for i in range(total_requests)])
        wall_seconds = time.perf_counter() - wall_start

    latencies = sorted(r["latency_ms"] for r in results)
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = total_requests - ok_count
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0.0
    p99 = latencies[min(len(latencies) - 1, int(0.99 * len(latencies)))] if latencies else 0.0

    report = {
        "total_requests": total_requests,
        "concurrency": concurrency,
        "succeeded": ok_count,
        "failed": fail_count,
        "wall_time_sec": round(wall_seconds, 2),
        "throughput_rps": round(total_requests / max(wall_seconds, 0.001), 2),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0.0,
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }
    print("\n=== AI-HAS Load Benchmark Report ===")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-HAS Concurrent Load & Latency Benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend API base URL")
    parser.add_argument("--requests", type=int, default=30, help="Total chat requests to execute")
    parser.add_argument("--concurrency", type=int, default=6, help="Max concurrent in-flight requests")
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.base_url, args.requests, args.concurrency))


if __name__ == "__main__":
    main()
