#!/usr/bin/env python3
"""Run measured end-to-end delivery trials through the admin integration API."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def _percentile(values: list[int], percent: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent * len(ordered)) - 1)]


def _questions(args: argparse.Namespace) -> list[str]:
    questions = list(args.question)
    if args.questions_file:
        questions.extend(
            line.strip()
            for line in args.questions_file.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not questions:
        raise SystemExit("Provide --question or --questions-file")
    return [question for question in questions for _ in range(args.runs)]


def _runtime_api_url() -> str:
    runtime = dotenv_values(ROOT / ".run/laptop-demo/runtime.env")
    scheme = "https" if runtime.get("INTELLIKNOW_HTTPS", "1") != "0" else "http"
    host = runtime.get("INTELLIKNOW_API_HOST", "127.0.0.1")
    port = runtime.get("INTELLIKNOW_API_PORT", "8000")
    return f"{scheme}://{host}:{port}"


def main() -> None:
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, choices=("telegram", "teams"))
    parser.add_argument("--api-url", default=_runtime_api_url())
    parser.add_argument("--admin-password", default=values.get("ADMIN_PASSWORD"))
    parser.add_argument("--ca-cert", type=Path, default=ROOT / ".run/laptop-demo/tls/rootCA.pem")
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--questions-file", type=Path)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--target-ms", type=int, default=3000)
    parser.add_argument("--require-real-platform", action="store_true")
    args = parser.parse_args()
    if not args.admin_password:
        raise SystemExit("ADMIN_PASSWORD is required")
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    verify: str | bool = str(args.ca_cert) if args.ca_cert.exists() else True
    results: list[dict] = []
    with httpx.Client(
        base_url=args.api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {args.admin_password}"},
        verify=verify,
        timeout=30,
    ) as client:
        for number, question in enumerate(_questions(args), start=1):
            response = client.post(
                f"/admin/integrations/{args.channel}/test",
                json={"question": question},
            )
            response.raise_for_status()
            result = response.json()
            results.append(result)
            detail = f"  {result['error']}" if result.get("error") else ""
            print(
                f"{number:02d} {result['latency_ms']:4d} ms  "
                f"{result['status']:<8} {result.get('platform_mode') or '-':<5}  "
                f"{question}{detail}"
            )

    failures = [result for result in results if not result["ok"]]
    local_platform = [
        result
        for result in results
        if args.require_real_platform and result.get("platform_mode") == "local"
    ]
    unknown_platform = [
        result
        for result in results
        if args.require_real_platform
        and result["ok"]
        and result.get("platform_mode") not in {"real", "local"}
    ]
    latencies = [result["latency_ms"] for result in results if result["ok"]]
    if latencies:
        p50 = _percentile(latencies, 0.50)
        p95 = _percentile(latencies, 0.95)
        print(
            f"\n{args.channel}: n={len(latencies)}, p50={p50} ms, "
            f"p95={p95} ms, max={max(latencies)} ms, target={args.target_ms} ms"
        )
    else:
        p95 = None

    if failures:
        print(f"FAILED: {len(failures)} delivery trial(s) did not complete")
    if local_platform:
        print("FAILED: the stored destination is local Emulator, not real Teams")
    if unknown_platform:
        print("FAILED: the successful destination could not be verified as real")
    if p95 is not None and p95 > args.target_ms:
        print(f"FAILED: p95 {p95} ms exceeds {args.target_ms} ms")
    if (
        failures
        or local_platform
        or unknown_platform
        or (p95 is not None and p95 > args.target_ms)
    ):
        raise SystemExit(1)
    print("PASSED: delivery and latency acceptance gates")


if __name__ == "__main__":
    main()
