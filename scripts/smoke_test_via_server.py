#!/usr/bin/env python3
"""
Lamark smoke test v2 — measure decode tok/s via a running vLLM server.

This replaces the in-process model-load approach in smoke_test.py for
deployment scenarios. The model lives inside the NGC container; this script
runs on the host (or anywhere with network reach) and hits the OpenAI-compatible
endpoint.

Workflow:
  1) Start vLLM server:  ./scripts/vllm_server.sh --detach
  2) Wait for ready:     curl http://127.0.0.1:8000/v1/models
  3) Run this:           python scripts/smoke_test_via_server.py

Pass criteria identical to smoke_test.py:
  - HARD FAIL: median < 22 tok/s
  - WARN:      median < 25 tok/s
  - PASS:      median >= 25 tok/s
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

RIKKARTH_MEASURED_TOK_PER_S = 28.0
THEORETICAL_CEILING_TOK_PER_S = 91.0
HARD_FAIL_THRESHOLD = 22.0
WARN_THRESHOLD = 25.0

DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"


@dataclass
class Result:
    timestamp: str
    hostname: str
    endpoint: str
    model: str
    passed: bool
    severity: str
    metrics: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hard_fail_threshold_tok_s: float = HARD_FAIL_THRESHOLD
    rikkarth_reference_tok_s: float = RIKKARTH_MEASURED_TOK_PER_S


def http_post(url: str, payload: dict, timeout: int = 600) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(data)
            except json.JSONDecodeError:
                return resp.status, data
    except error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except error.URLError as e:
        return -1, f"URLError: {e.reason}"


def http_get(url: str, timeout: int = 10) -> tuple[int, str]:
    req = request.Request(url)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except (error.HTTPError, error.URLError) as e:
        return -1, str(e)


def check_ready(result: Result, endpoint: str, expected_model: str) -> bool:
    code, body = http_get(f"{endpoint}/models", timeout=5)
    if code != 200:
        result.notes.append(f"✗ {endpoint}/models returned {code}: {body[:200]}")
        return False
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        result.notes.append(f"✗ /models returned non-JSON: {body[:200]!r}")
        return False
    served = [m.get("id") for m in data.get("data", [])]
    if expected_model not in served:
        result.notes.append(
            f"⚠ expected model {expected_model!r} not served; available: {served}. "
            "Will still run benchmark but timing may not match v3 assumptions."
        )
    result.notes.append(f"✓ endpoint reachable; serving: {served}")
    return True


def benchmark(
    result: Result,
    endpoint: str,
    model: str,
    prompt: str,
    max_new_tokens: int,
    n_warmup: int,
    n_runs: int,
) -> None:
    payload_template: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_new_tokens,
        "stream": False,
    }
    for _ in range(n_warmup):
        code, _ = http_post(f"{endpoint}/chat/completions", payload_template)
        if code != 200:
            result.notes.append(f"✗ warmup request failed (code {code})")
            return

    for i in range(n_runs):
        t0 = time.time()
        code, body = http_post(f"{endpoint}/chat/completions", payload_template)
        dt = time.time() - t0
        if code != 200 or not isinstance(body, dict):
            result.notes.append(f"✗ run {i} failed (code {code}): {str(body)[:200]}")
            return
        usage = body.get("usage", {})
        n_tokens = usage.get("completion_tokens") or 0
        if not n_tokens:
            # Fallback: count from content (rough)
            content = body["choices"][0]["message"]["content"]
            n_tokens = max(1, len(content.split()))
        tok_s = n_tokens / dt
        result.runs.append(
            {"run": i, "tokens": n_tokens, "seconds": round(dt, 3), "tok_per_s": round(tok_s, 2)}
        )

    if result.runs:
        sorted_tps = sorted(r["tok_per_s"] for r in result.runs)
        median = sorted_tps[len(sorted_tps) // 2]
        result.metrics["median_tok_per_s"] = round(median, 2)
        result.metrics["min_tok_per_s"] = round(min(sorted_tps), 2)
        result.metrics["max_tok_per_s"] = round(max(sorted_tps), 2)


def evaluate(result: Result) -> None:
    fatal = [n for n in result.notes if n.startswith("✗")]
    if fatal:
        result.severity = "fail"
        result.passed = False
        return
    median = result.metrics.get("median_tok_per_s")
    if median is None:
        result.severity = "fail"
        result.passed = False
        result.notes.append("✗ no successful runs — cannot evaluate")
        return
    if median < HARD_FAIL_THRESHOLD:
        result.severity = "fail"
        result.passed = False
        result.notes.append(
            f"✗ HARD FAIL median {median:.1f} tok/s < {HARD_FAIL_THRESHOLD}. "
            f"Expected ~{RIKKARTH_MEASURED_TOK_PER_S} (Rikkarth). "
            "DO NOT proceed to Phase 1 until resolved."
        )
    elif median < WARN_THRESHOLD:
        result.severity = "warn"
        result.passed = True
        result.notes.append(
            f"⚠ WARN median {median:.1f} tok/s below comfort. "
            "Investigate power cap / thermal / quantization config."
        )
    else:
        result.severity = "pass"
        result.passed = True
        result.notes.append(
            f"✓ PASS median {median:.1f} tok/s (envelope: "
            f"hard-fail {HARD_FAIL_THRESHOLD}, warn {WARN_THRESHOLD}, "
            f"ref {RIKKARTH_MEASURED_TOK_PER_S}, ceiling {THEORETICAL_CEILING_TOK_PER_S})"
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--prompt",
        default="Explain the difference between LoRA and DoRA in one paragraph.",
    )
    p.add_argument("--max-new-tokens", type=int, default=128)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    result = Result(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        hostname=platform.node(),
        endpoint=args.endpoint,
        model=args.model,
        passed=False,
        severity="unknown",
    )

    print(f"[lamark smoke v2] endpoint={args.endpoint} model={args.model}")
    if not check_ready(result, args.endpoint, args.model):
        evaluate(result)
    else:
        print(f"[lamark smoke v2] running {args.warmup} warmup + {args.runs} timed runs...")
        benchmark(
            result,
            args.endpoint,
            args.model,
            args.prompt,
            args.max_new_tokens,
            args.warmup,
            args.runs,
        )
        evaluate(result)

    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(result.severity, "?")
    print("\n" + "=" * 70)
    print(f"{icon} {result.severity.upper()}")
    print("=" * 70)
    for n in result.notes:
        print(f"  {n}")
    if result.metrics:
        print("Metrics:")
        for k, v in result.metrics.items():
            print(f"  {k}: {v}")
    if result.runs:
        print("Per-run:")
        for r in result.runs:
            print(f"  run {r['run']}: {r['tok_per_s']} tok/s ({r['tokens']} tokens / {r['seconds']}s)")
    print("=" * 70)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(asdict(result), indent=2))
        print(f"\nReport written to {args.output}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
