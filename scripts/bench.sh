#!/bin/bash
# `lamark bench` — measure tok/s + TTFT of the local vLLM endpoint.
#
# Mirrors the methodology of the reference repo
# (ZengboJamesWang/dgx-spark-vllm-qwen3.6-35b-a3b-dflash): generate 512 tokens
# at multiple prompt depths, report sustained throughput.
#
# Outputs a markdown summary under docs/benchmarks/<YYYY-MM-DD>-<label>.md so
# we can attribute speedups to individual perf changes (flags / FP8 / DFlash).
#
# Usage: bash scripts/bench.sh [label] [endpoint]
#   label    — short slug for the output filename (e.g. "stage1-flags").
#              Defaults to a timestamp.
#   endpoint — OpenAI-compatible /v1 base. Defaults to http://127.0.0.1:8000/v1.
set -euo pipefail

LABEL="${1:-$(date +%H%M%S)}"
ENDPOINT="${2:-http://127.0.0.1:8000/v1}"
MODEL="${LAMARK_BENCH_MODEL:-qwen-base}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/docs/benchmarks"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/$(date +%Y-%m-%d)-${LABEL}.md"

# Hand off everything to Python — bash is bad at timing and JSON.
python3 - "$ENDPOINT" "$MODEL" "$OUT_FILE" "$LABEL" <<'PY'
import json, sys, time, urllib.request, urllib.error, statistics

endpoint, model, out_file, label = sys.argv[1:5]

PROMPTS = [
    ("short",   "Write a 500-word story about an autumn forest."),
    ("medium",  "Write a 500-word essay about machine learning. " +
                "Cover supervised, unsupervised, and reinforcement learning. " * 8),
    ("long",    "Write a 500-word summary. " +
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 32),
]
MAX_TOKENS = 512
N_RUNS = 3   # per prompt — keeps total under ~5 min on a 30 tok/s baseline

def http_post(url, body, headers=None, timeout=300):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def http_post_stream(url, body, timeout=300):
    """Yield (timestamp, line_bytes) for each SSE 'data: ...' line."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            yield time.time(), raw

def measure_one(name, prompt):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.7,
        "stream": False,
    }
    t0 = time.time()
    resp_bytes = http_post(f"{endpoint}/chat/completions", body)
    elapsed = time.time() - t0
    resp = json.loads(resp_bytes)
    u = resp.get("usage", {})
    return {
        "prompt_tokens":     u.get("prompt_tokens", 0),
        "completion_tokens": u.get("completion_tokens", 0),
        "total_tokens":      u.get("total_tokens", 0),
        "elapsed_s":         elapsed,
        "tok_per_s":         u.get("completion_tokens", 0) / elapsed if elapsed > 0 else 0.0,
    }

def measure_ttft(prompt):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32,
        "stream": True,
    }
    t0 = time.time()
    first = None
    for ts, raw in http_post_stream(f"{endpoint}/chat/completions", body):
        line = raw.decode("utf-8", errors="ignore").strip()
        if line.startswith("data: ") and "[DONE]" not in line:
            first = ts
            break
    return (first - t0) if first else None

def model_metadata():
    try:
        with urllib.request.urlopen(f"{endpoint}/models", timeout=5) as r:
            data = json.load(r)
        for m in data.get("data", []):
            if m.get("id") == model:
                return m
        return data.get("data", [{}])[0]
    except Exception:
        return {}

print(f"[bench] endpoint={endpoint} model={model} label={label}")
meta = model_metadata()
print(f"[bench] model root: {meta.get('root','?')}  max_model_len: {meta.get('max_model_len','?')}")

results = []
ttft_samples = []
for name, prompt in PROMPTS:
    print(f"\n[bench] {name}: 3 runs ...", flush=True)
    runs = []
    for i in range(N_RUNS):
        r = measure_one(name, prompt)
        runs.append(r)
        print(f"  run {i+1}: prompt={r['prompt_tokens']:>5}  "
              f"compl={r['completion_tokens']:>4}  "
              f"wall={r['elapsed_s']:6.2f}s  "
              f"tok/s={r['tok_per_s']:5.1f}",
              flush=True)
    results.append((name, prompt, runs))
    # TTFT — once per prompt (no need to average; it's stable in spec-decode regimes)
    ttft = measure_ttft(prompt)
    if ttft is not None:
        ttft_samples.append((name, ttft))
        print(f"  TTFT: {ttft*1000:.0f}ms")

print(f"\n[bench] writing summary to {out_file}")
with open(out_file, "w") as f:
    f.write(f"# Lamark vLLM benchmark — {label}\n\n")
    f.write(f"- date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"- endpoint: {endpoint}\n")
    f.write(f"- model: {model}\n")
    f.write(f"- model root: {meta.get('root','?')}\n")
    f.write(f"- max_model_len: {meta.get('max_model_len','?')}\n\n")

    f.write("## Throughput (3 runs each, 512 tokens out)\n\n")
    f.write("| Prompt | Prompt tok | Compl tok | Wall avg | **tok/s avg** | tok/s peak |\n")
    f.write("|---|---|---|---|---|---|\n")
    overall = []
    for name, _prompt, runs in results:
        p_tok = runs[0]["prompt_tokens"]
        c_tok = int(statistics.mean(r["completion_tokens"] for r in runs))
        wall  = statistics.mean(r["elapsed_s"] for r in runs)
        tps   = [r["tok_per_s"] for r in runs]
        avg   = statistics.mean(tps)
        peak  = max(tps)
        overall.extend(tps)
        f.write(f"| {name} | {p_tok} | {c_tok} | {wall:.2f}s | **{avg:.1f}** | {peak:.1f} |\n")
    if overall:
        f.write(f"\n**Overall avg: {statistics.mean(overall):.1f} tok/s  "
                f"(peak {max(overall):.1f})**\n\n")

    if ttft_samples:
        f.write("## Time to first token\n\n")
        f.write("| Prompt | TTFT |\n|---|---|\n")
        for name, t in ttft_samples:
            f.write(f"| {name} | {t*1000:.0f}ms |\n")
        f.write("\n")

    f.write("## Raw runs\n\n```json\n")
    f.write(json.dumps([
        {"prompt": name, "runs": runs}
        for name, _prompt, runs in results
    ], indent=2))
    f.write("\n```\n")

print("\n[bench] done.")
PY

echo ""
echo "Wrote: $OUT_FILE"
