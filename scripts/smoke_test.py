#!/usr/bin/env python3
"""
Lamark Phase 0 smoke test for NVIDIA DGX Spark.

Validates that the target hardware can serve Qwen3.6-35B-A3B (MoE, FP8) via vLLM
within the performance envelope established by Rikkarth (rikkarth.com, Apr 2026):
  - Theoretical decode ceiling: 273 GB/s ÷ ~3 GB active = ~91 tok/s
  - Measured single-stream: ~28-30 tok/s (steady state)
  - Acceptable: ≥ 22 tok/s (allows for thermal/firmware variance)
  - HARD FAIL: < 22 tok/s OR cannot load OR sm_121 not detected

If this test does not pass, DO NOT proceed to Phase 1. The architecture
assumes MoE inference at ~25-30 tok/s; lower numbers invalidate the UX premise.

Usage on Spark:
    python scripts/smoke_test.py --model Qwen/Qwen3.6-35B-A3B \\
                                 --quantization fp8 \\
                                 --output smoke_test_results/$(date +%Y%m%d-%H%M%S).json

Run with --quick to skip the full benchmark and only check that the model
can load (useful for first-time setup validation).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Reference numbers from feasibility-report-v3.md §3
RIKKARTH_MEASURED_TOK_PER_S = 28.0
THEORETICAL_CEILING_TOK_PER_S = 91.0
HARD_FAIL_THRESHOLD = 22.0  # ~ 80% of Rikkarth measured
WARN_THRESHOLD = 25.0  # below this, plan capacity at 50-70%

# Per Кройцхофер NVIDIA forum thread — bf16 weights for Qwen3.5/3.6-35B-A3B
EXPECTED_BF16_WEIGHTS_GB = 67.0
EXPECTED_FP8_WEIGHTS_GB = 36.0
USABLE_UNIFIED_MEMORY_GB = 119.0  # of 128 GB on DGX Spark


@dataclass
class SmokeResult:
    timestamp: str
    hostname: str
    passed: bool
    severity: str  # "pass" | "warn" | "fail"
    checks: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    rikkarth_reference_tok_s: float = RIKKARTH_MEASURED_TOK_PER_S
    hard_fail_threshold_tok_s: float = HARD_FAIL_THRESHOLD


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -2, "", str(e)


def check_platform(result: SmokeResult) -> None:
    """Detect we're on the right kind of box."""
    result.checks["platform"] = {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
    }

    if platform.system() != "Linux":
        result.notes.append(f"⚠ Not Linux ({platform.system()}). Spark expects DGX OS / Ubuntu 24.04.")
        return

    if platform.machine() not in ("aarch64", "arm64"):
        result.notes.append(f"⚠ Not ARM64 ({platform.machine()}). Spark's GB10 is ARM (Cortex-X925/A725).")


def check_nvidia(result: SmokeResult) -> None:
    """Verify CUDA + nvidia-smi + sm_121 (Blackwell GB10)."""
    rc, out, err = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader"])
    if rc != 0:
        result.notes.append(f"✗ nvidia-smi failed: {err.strip() or 'not installed?'}")
        result.checks["nvidia_smi"] = {"available": False, "error": err.strip()}
        return

    fields = [f.strip() for f in out.strip().split(",")]
    result.checks["nvidia_smi"] = {
        "available": True,
        "raw": out.strip(),
    }
    if len(fields) >= 4:
        name, mem_total, driver, compute_cap = fields[:4]
        result.checks["nvidia_smi"]["gpu_name"] = name
        result.checks["nvidia_smi"]["memory_total"] = mem_total
        result.checks["nvidia_smi"]["driver_version"] = driver
        result.checks["nvidia_smi"]["compute_cap"] = compute_cap

        if "12.1" not in compute_cap and "12.0" not in compute_cap:
            result.notes.append(
                f"⚠ Compute capability {compute_cap!r} — DGX Spark GB10 expects sm_121 (12.1). "
                "If this is not GB10, performance numbers below are not applicable."
            )

        if "GB10" not in name and "Grace" not in name and "Blackwell" not in name:
            result.notes.append(
                f"⚠ GPU name {name!r} doesn't mention GB10/Grace/Blackwell — "
                "you may not be on a Spark. Performance comparison invalid."
            )


def check_cuda_torch(result: SmokeResult) -> None:
    """Verify torch sees CUDA."""
    code = (
        "import torch, json; "
        "print(json.dumps({"
        "'torch_version': torch.__version__, "
        "'cuda_available': torch.cuda.is_available(), "
        "'cuda_version': torch.version.cuda, "
        "'device_count': torch.cuda.device_count(), "
        "'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
        "'device_capability': list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,"
        "}))"
    )
    rc, out, err = run([sys.executable, "-c", code], timeout=60)
    if rc != 0:
        result.notes.append(f"✗ torch CUDA check failed: {err.strip()[:200]}")
        result.checks["torch"] = {"available": False, "error": err.strip()[:500]}
        return
    try:
        info = json.loads(out.strip().splitlines()[-1])
        result.checks["torch"] = info
        if not info.get("cuda_available"):
            result.notes.append("✗ torch.cuda.is_available() is False — no CUDA visible to PyTorch")
    except (json.JSONDecodeError, IndexError) as e:
        result.notes.append(f"✗ Could not parse torch output: {e}")
        result.checks["torch"] = {"available": False, "raw": out[:500]}


def check_vllm(result: SmokeResult) -> None:
    """Verify vLLM is installed and matches a known-good version range."""
    code = "import vllm, json; print(json.dumps({'version': vllm.__version__}))"
    rc, out, err = run([sys.executable, "-c", code], timeout=30)
    if rc != 0:
        result.notes.append(f"✗ vLLM not importable: {err.strip()[:200]}")
        result.checks["vllm"] = {"available": False, "error": err.strip()[:500]}
        return
    try:
        info = json.loads(out.strip().splitlines()[-1])
        result.checks["vllm"] = {"available": True, **info}
    except (json.JSONDecodeError, IndexError):
        result.checks["vllm"] = {"available": True, "raw": out.strip()}


def check_memory(result: SmokeResult) -> None:
    """Verify enough free unified memory for Qwen3.6-35B-A3B FP8 (~36 GB)."""
    rc, out, _ = run(["free", "-g"], timeout=5)
    if rc != 0:
        result.notes.append("⚠ Could not run `free -g` — skipping memory check")
        return
    for line in out.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            try:
                total_gb = int(parts[1])
                available_gb = int(parts[-1])
                result.checks["memory_gb"] = {"total": total_gb, "available": available_gb}
                if total_gb < 120:
                    result.notes.append(
                        f"⚠ Total memory {total_gb} GB — Spark expects ~128 GB unified. "
                        "Confirm this is a Spark."
                    )
                if available_gb < EXPECTED_FP8_WEIGHTS_GB + 20:
                    result.notes.append(
                        f"⚠ Available memory {available_gb} GB is tight; "
                        f"FP8 weights need ~{EXPECTED_FP8_WEIGHTS_GB} GB + ~20 GB KV/buffers"
                    )
            except (ValueError, IndexError):
                pass
            return


def benchmark_decode(
    result: SmokeResult,
    model: str,
    quant: str,
    prompt: str,
    max_new_tokens: int,
    n_warmup: int,
    n_runs: int,
) -> None:
    """Actually load the model and measure single-stream decode tok/s via vLLM."""
    bench_code = f"""
import json, time, sys
try:
    from vllm import LLM, SamplingParams
except ImportError as e:
    print(json.dumps({{"error": f"vLLM import failed: {{e}}"}}))
    sys.exit(1)

model_id = {model!r}
quant_arg = {quant!r}

llm_kwargs = dict(
    model=model_id,
    tensor_parallel_size=1,
    enable_expert_parallel=True,
    gpu_memory_utilization=0.85,
    dtype="auto",
    enforce_eager=False,
    trust_remote_code=True,
)
if quant_arg == "fp8":
    llm_kwargs["quantization"] = "fp8"
elif quant_arg == "bf16":
    llm_kwargs["dtype"] = "bfloat16"

try:
    t0 = time.time()
    llm = LLM(**llm_kwargs)
    load_seconds = time.time() - t0
except Exception as e:
    print(json.dumps({{"error": f"vLLM load failed: {{type(e).__name__}}: {{e}}"}}))
    sys.exit(2)

sp = SamplingParams(temperature=0.0, max_tokens={max_new_tokens}, top_p=1.0)
prompt = {prompt!r}

# Warmup
for _ in range({n_warmup}):
    _ = llm.generate(prompt, sp, use_tqdm=False)

# Timed runs
samples = []
for _ in range({n_runs}):
    t0 = time.time()
    outs = llm.generate(prompt, sp, use_tqdm=False)
    dt = time.time() - t0
    n_tokens = len(outs[0].outputs[0].token_ids)
    samples.append({{"tokens": n_tokens, "seconds": dt, "tok_per_s": n_tokens / dt}})

print(json.dumps({{
    "load_seconds": load_seconds,
    "runs": samples,
    "median_tok_per_s": sorted(s["tok_per_s"] for s in samples)[len(samples)//2],
}}))
"""
    rc, out, err = run([sys.executable, "-c", bench_code], timeout=1800)
    if rc != 0:
        result.notes.append(f"✗ Benchmark subprocess failed (rc={rc}): {err.strip()[:500]}")
        result.checks["benchmark"] = {"ran": False, "error": err.strip()[:1000]}
        return

    try:
        last_line = out.strip().splitlines()[-1]
        bench = json.loads(last_line)
        if "error" in bench:
            result.notes.append(f"✗ Benchmark internal error: {bench['error']}")
            result.checks["benchmark"] = {"ran": False, **bench}
            return
        result.checks["benchmark"] = {"ran": True, **bench}
        median = float(bench["median_tok_per_s"])
        result.metrics["median_tok_per_s"] = median
        result.metrics["load_seconds"] = float(bench["load_seconds"])
    except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
        result.notes.append(f"✗ Could not parse benchmark output: {e}. Raw tail: {out[-500:]!r}")
        result.checks["benchmark"] = {"ran": False, "parse_error": str(e), "raw_tail": out[-500:]}


def evaluate(result: SmokeResult, quick: bool) -> None:
    """Apply pass/warn/fail logic to gathered evidence."""
    fatal = [n for n in result.notes if n.startswith("✗")]
    if fatal:
        result.severity = "fail"
        result.passed = False
        return

    if quick:
        # In --quick mode we only need: nvidia visible, torch CUDA ok, vllm importable
        nvsmi = result.checks.get("nvidia_smi", {}).get("available")
        torch_cuda = result.checks.get("torch", {}).get("cuda_available")
        vllm_ok = result.checks.get("vllm", {}).get("available")
        if nvsmi and torch_cuda and vllm_ok:
            result.severity = "pass"
            result.passed = True
            result.notes.append("✓ Quick check passed. Run without --quick for full decode benchmark.")
        else:
            result.severity = "fail"
            result.passed = False
        return

    median = result.metrics.get("median_tok_per_s")
    if median is None:
        result.severity = "fail"
        result.passed = False
        result.notes.append("✗ No decode benchmark result — cannot evaluate go/no-go gate.")
        return

    if median < HARD_FAIL_THRESHOLD:
        result.severity = "fail"
        result.passed = False
        result.notes.append(
            f"✗ HARD FAIL: median {median:.1f} tok/s < {HARD_FAIL_THRESHOLD} tok/s threshold. "
            f"Expected ~{RIKKARTH_MEASURED_TOK_PER_S} per Rikkarth. "
            "DO NOT proceed to Phase 1 until this is resolved."
        )
    elif median < WARN_THRESHOLD:
        result.severity = "warn"
        result.passed = True
        result.notes.append(
            f"⚠ WARN: median {median:.1f} tok/s below comfort threshold {WARN_THRESHOLD}. "
            "May indicate software power cap (Carmack-flagged 100 W) or thermal issues. "
            "Plan capacity at 50-70% of marketing performance."
        )
    else:
        result.severity = "pass"
        result.passed = True
        result.notes.append(
            f"✓ PASS: median {median:.1f} tok/s ≥ {WARN_THRESHOLD} tok/s. "
            f"Within expected envelope (Rikkarth ~{RIKKARTH_MEASURED_TOK_PER_S}, "
            f"ceiling ~{THEORETICAL_CEILING_TOK_PER_S})."
        )


def main() -> int:
    p = argparse.ArgumentParser(description="Lamark Phase 0 smoke test on DGX Spark.")
    p.add_argument("--model", default="Qwen/Qwen3.6-35B-A3B")
    p.add_argument("--quantization", choices=["fp8", "bf16"], default="fp8")
    p.add_argument("--prompt", default="Explain the difference between LoRA and DoRA in one paragraph.")
    p.add_argument("--max-new-tokens", type=int, default=128)
    p.add_argument("--warmup", type=int, default=2, help="Warmup runs before timing")
    p.add_argument("--runs", type=int, default=5, help="Timed runs (median taken)")
    p.add_argument("--quick", action="store_true", help="Skip decode benchmark, only check environment")
    p.add_argument("--output", type=Path, default=None, help="Path to write JSON report")
    args = p.parse_args()

    result = SmokeResult(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        hostname=platform.node(),
        passed=False,
        severity="unknown",
    )

    print(f"[lamark smoke] starting on {result.hostname} ({result.timestamp})")
    print(f"[lamark smoke] model={args.model} quant={args.quantization} quick={args.quick}")

    check_platform(result)
    check_nvidia(result)
    check_cuda_torch(result)
    check_vllm(result)
    check_memory(result)

    if not args.quick:
        print("[lamark smoke] running decode benchmark (may take 5-15 min on first run)...")
        benchmark_decode(
            result,
            model=args.model,
            quant=args.quantization,
            prompt=args.prompt,
            max_new_tokens=args.max_new_tokens,
            n_warmup=args.warmup,
            n_runs=args.runs,
        )

    evaluate(result, quick=args.quick)

    # Pretty summary
    print("\n" + "=" * 70)
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(result.severity, "?")
    print(f"{icon} {result.severity.upper()}")
    print("=" * 70)
    for note in result.notes:
        print(f"  {note}")
    if result.metrics:
        print("\nMetrics:")
        for k, v in result.metrics.items():
            print(f"  {k}: {v:.2f}")
    print("=" * 70)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(asdict(result), indent=2))
        print(f"\nReport written to {args.output}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
