#!/bin/bash
# Lamark nightly retrain.
#
# Pipeline:
#   1. Build curation plan from the archive (only pairs added since last run).
#   2. If empty → nothing to learn → exit 0.
#   3. Train a fresh LoRA adapter inside the lamark/vllm container.
#   4. Hot-load the new adapter into the running vLLM via /v1/load_lora_adapter.
#   5. Run the eval-gate against the new adapter.
#   6. If gate passes → swap default model alias to point at the new adapter.
#      If gate fails → unload the new adapter, keep the previous one active.
#
# All output goes to ~/.lamark/logs/nightly-train-<date>.log.
# Designed to be invoked from a systemd timer or crontab line:
#   0 3 * * *  /home/jetbrains/lamark-agent/scripts/lamark-nightly-train.sh
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://127.0.0.1:8000/v1}"
ADAPTER_DIR="${LAMARK_ADAPTER_DIR:-$LAMARK_HOME/adapters}"

# Resolve base model path from the registry rather than hardcoding the
# BF16 entry — Lamark may switch between BF16 and FP8 (Stage 2 of the
# perf push), and the trainer must follow the registry-declared default.
# Override with LAMARK_BASE_MODEL=/abs/path for ad-hoc runs against a
# non-default model.
export HERMES_HOME_PRE="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
export LAMARK_HOME
if [ -n "${LAMARK_BASE_MODEL:-}" ]; then
    BASE_MODEL_DIR="$LAMARK_BASE_MODEL"
else
    # Resolve the training base via the registry. The serving stack may
    # be running a quantized model (FP8 / INT4) but HF Transformers
    # refuses to fine-tune those — see the ValueError on
    # QuantizationMethod.FP8. Each entry can declare `train_with:` to
    # point at a sibling registry entry whose hf_id we use instead.
    # `model.default` in config.yaml may be the registry slug or a
    # served-model alias; on KeyError, fall back to the tier-S default.
    BASE_MODEL_DIR=$(PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" - <<'PY'
import os, yaml
from lamark.registry import load_registry, get_model, ModelEntry

lamark_home = os.environ["LAMARK_HOME"]
hermes_home = os.environ["HERMES_HOME_PRE"]
cfg = yaml.safe_load(open(f"{hermes_home}/config.yaml").read()) or {}
name = (cfg.get("model") or {}).get("default") or ""

entry = None
try:
    entry = get_model(name)
except Exception:
    for n, e in load_registry().items():
        if isinstance(e, ModelEntry) and e.tier == "S" and e.default_for_tier:
            entry = e
            break

# If the resolved entry declares a sibling training base (e.g. FP8 →
# BF16), prefer that. Otherwise train against the entry's own weights.
if entry is not None and entry.serving.train_with:
    try:
        train_entry = get_model(entry.serving.train_with)
        entry = train_entry
    except Exception:
        pass  # fall through to entry itself

if entry is not None:
    flat = entry.hf_id.replace("/", "_")
    print(f"{lamark_home}/models/hf/{flat}")
PY
)
    BASE_MODEL_DIR="${BASE_MODEL_DIR:-$LAMARK_HOME/models/hf/Qwen_Qwen3.6-35B-A3B}"
fi

LOG_DIR="$LAMARK_HOME/logs"
mkdir -p "$LOG_DIR" "$ADAPTER_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/nightly-train-$TS.log"

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
fail() { log "FAIL: $*"; notify_failure "$1"; exit 1; }

# Send a Telegram message to the bot owner. Uses the same bot token the
# gateway polls with, plus TELEGRAM_HOME_CHANNEL (the owner's chat_id —
# already populated by the gateway setup wizard). Pure HTTPS POST, no
# Hermes import required, so the trainer doesn't have to share venv
# state with the running daemon.
#
# All notification failures are SILENT — we never let a failed Telegram
# call break the training pipeline.
notify_user() {
    # Sends one Telegram message in HTML parse mode (more forgiving than
    # Markdown for content with underscores in identifiers / paths).
    local body="$1"
    local token chat_id env_file="${HERMES_HOME:-$LAMARK_HOME/hermes-home}/.env"
    [ -f "$env_file" ] || return 0
    token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    chat_id=$(grep -E '^TELEGRAM_HOME_CHANNEL=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "$token" ] && [ -n "$chat_id" ] || return 0
    curl -sS --max-time 10 "https://api.telegram.org/bot$token/sendMessage" \
        -d "chat_id=$chat_id" \
        -d "parse_mode=HTML" \
        --data-urlencode "text=$body" \
        >> "$LOG" 2>&1 || true
}

notify_success() {
    local n_pairs="$1" adapter_name="$2"
    notify_user "🎓 <b>Lamark training complete</b>
• Pairs used: ${n_pairs}
• Adapter: <code>${adapter_name}</code>
• Eval gate: ✓ promoted
• Active after the next vLLM restart"
}

notify_rejection() {
    local n_pairs="$1" adapter_name="$2"
    notify_user "⚠️ <b>Lamark training: adapter rejected</b>
• Pairs used: ${n_pairs}
• Adapter: <code>${adapter_name}</code>
• Eval gate failed — base model unchanged"
}

notify_skip() {
    local n_pairs="$1" threshold="$2"
    notify_user "ℹ️ <b>Lamark training skipped</b>
• Pairs accumulated: ${n_pairs} / ${threshold} threshold
• Will retry on next scheduled run"
}

notify_failure() {
    local reason="$1"
    notify_user "❌ <b>Lamark training failed</b>
• Reason: <code>${reason}</code>
• Check log: <code>$LOG</code>"
}

log "=== Lamark nightly retrain starting ==="

# --- 0. Read training config from $HERMES_HOME/config.yaml -----------------
# Hybrid trigger: timer fires per `training.frequency` (set by setup wizard),
# but the actual retrain runs only when `training.min_pairs` are accumulated
# since the last successful retrain. Lets the user say "check daily, but
# don't waste GPU if I added 3 messages."
#
# Override via env vars LAMARK_TRAIN_MIN_PAIRS / LAMARK_TRAIN_FORCE for ad-hoc
# manual invocations (`lamark train --now --force`).
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
TRAIN_MIN_PAIRS="${LAMARK_TRAIN_MIN_PAIRS:-}"
TRAIN_FORCE="${LAMARK_TRAIN_FORCE:-0}"

if [ -z "$TRAIN_MIN_PAIRS" ] && [ -f "$HERMES_HOME/config.yaml" ]; then
    TRAIN_MIN_PAIRS=$(PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" -c "
import yaml
try:
    cfg = yaml.safe_load(open(r'$HERMES_HOME/config.yaml').read()) or {}
    t = (cfg.get('training') or {})
    print(int(t.get('min_pairs', 50)))
except Exception:
    print(50)
")
fi
TRAIN_MIN_PAIRS="${TRAIN_MIN_PAIRS:-50}"
log "Threshold: min_pairs=${TRAIN_MIN_PAIRS}, force=${TRAIN_FORCE}"

# --- 1. Build plan ---------------------------------------------------------
PLAN="$LAMARK_HOME/train-plan-nightly.jsonl"
log "Building plan -> $PLAN"
PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" - <<PY >> "$LOG" 2>&1
import json
from pathlib import Path
try:
    from lamark.train.curation import build_nightly_plan
    plan = build_nightly_plan()
    out = Path("$PLAN")
    with out.open("w", encoding="utf-8") as f:
        for rec in plan.records:
            f.write(json.dumps({"messages": rec["messages"]}, ensure_ascii=False) + "\n")
    print(f"plan has {len(plan.records)} records")
except Exception as e:
    # Curation pipeline not yet wired: fall back to dense identity + session seeds.
    print(f"curation fallback: {e}")
    from lamark.bootstrap.seed_identity_dense import _build_pairs as dense
    from lamark.bootstrap.seed_session import _build_pairs as sess
    pairs = dense() + sess()
    out = Path("$PLAN")
    with out.open("w", encoding="utf-8") as f:
        for p in pairs:
            rec = {"messages": [
                {"role": "user", "content": p.question},
                {"role": "assistant", "content": p.answer},
            ]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"fallback plan: {len(pairs)} pairs")
PY

if [ ! -s "$PLAN" ]; then
    log "Plan empty — nothing to train on. Exiting."
    exit 0
fi
N_PAIRS=$(wc -l < "$PLAN" | tr -d ' ')
log "Plan: $N_PAIRS pairs"

# --- 1b. Threshold check ---------------------------------------------------
# Skip retrain if not enough new content. Records `--force` override exempts
# (used by `lamark train --now --force` for testing/demos).
if [ "$TRAIN_FORCE" != "1" ] && [ "$N_PAIRS" -lt "$TRAIN_MIN_PAIRS" ]; then
    log "SKIP: only $N_PAIRS pairs accumulated, below threshold $TRAIN_MIN_PAIRS."
    log "Override with --force or lower training.min_pairs in config.yaml."
    # Record a "skipped" marker so `lamark train --status` can show it
    echo "{\"ts\":\"$TS\",\"action\":\"skipped\",\"n_pairs\":$N_PAIRS,\"threshold\":$TRAIN_MIN_PAIRS}" \
        >> "$LAMARK_HOME/train-history.jsonl"
    # Skipped runs only notify if the user explicitly forced via `train --now`
    # — daily cron-driven skips on an empty archive would be spammy.
    if [ "$TRAIN_FORCE" = "1" ]; then
        notify_skip "$N_PAIRS" "$TRAIN_MIN_PAIRS"
    fi
    exit 0
fi

# --- 2. Train --------------------------------------------------------------
ADAPTER_NAME="nightly-$TS"
log "Training adapter $ADAPTER_NAME (this takes 2-10 minutes)..."

# The dispatcher runs INSIDE the container with $LAMARK_HOME mounted at
# /workspace/.lamark. Translate the host-side BASE_MODEL_DIR (under
# $LAMARK_HOME on the host) to its corresponding container path. We
# always derived BASE_MODEL_DIR under $LAMARK_HOME above, so this
# substitution covers every supported entry from the registry.
CONTAINER_BASE_MODEL="${BASE_MODEL_DIR/#$LAMARK_HOME/\/workspace\/.lamark}"
log "Base model (host): $BASE_MODEL_DIR"
log "Base model (container): $CONTAINER_BASE_MODEL"

# Free the GPU so the training container can fit. We stop the production
# serve container (lamark-vllm — see scripts/cmd/serve.sh) and remember to
# restart it via `lamark serve start` once the adapter is saved. Note:
# this DOES interrupt active chats for the training duration; an opt-in
# zero-downtime path via vLLM /v1/load_lora_adapter would be cleaner but
# is deferred until DFlash+LoRA combo stability lands upstream.
PRIOR_VLLM_RUNNING=0
if docker ps --filter "name=lamark-vllm" -q | grep -q .; then
    PRIOR_VLLM_RUNNING=1
    log "Stopping production vLLM (lamark-vllm) to free GPU for training..."
    docker stop lamark-vllm >> "$LOG" 2>&1 || true
    docker rm   lamark-vllm >> "$LOG" 2>&1 || true
    sleep 5
fi

docker run --rm --name lamark-train-$TS \
  --gpus all --ipc=host \
  --ulimit memlock=-1 --ulimit stack=67108864 --shm-size=16g \
  -e LAMARK_HOME=/workspace/.lamark \
  -e LAMARK_MODEL_DIR=/workspace/models \
  -e TORCH_CUDA_ARCH_LIST=12.1 \
  -v "$REPO":/workspace/lamark-agent \
  -v "$LAMARK_HOME":/workspace/.lamark \
  -v "$LAMARK_HOME/models":/workspace/models \
  -v "$ADAPTER_DIR":/workspace/adapters \
  -w /workspace/lamark-agent \
  -e PYTHONPATH=/workspace/lamark-agent/src \
  lamark/vllm:25.10 \
  bash -c "pip uninstall -y flash-attn flash_attn 2>/dev/null; pip install --no-deps 'torchao>=0.16' 2>&1 | tail -1; python /workspace/lamark-agent/src/lamark/train/dispatcher_spark.py --pairs-jsonl /workspace/.lamark/train-plan-nightly.jsonl --base-model $CONTAINER_BASE_MODEL --adapter-name $ADAPTER_NAME --lora-rank 16 --num-epochs 5 --learning-rate 2e-4 --per-device-batch-size 1 --grad-accum-steps 4 --output-dir /workspace/adapters" \
  >> "$LOG" 2>&1 || fail "training container exited non-zero"

if [ ! -f "$ADAPTER_DIR/$ADAPTER_NAME/adapter_model.safetensors" ]; then
    fail "adapter not saved at $ADAPTER_DIR/$ADAPTER_NAME"
fi
log "Adapter saved: $ADAPTER_DIR/$ADAPTER_NAME"

# --- 3. Restart vLLM with new adapter --------------------------------------
# serve.sh auto-discovers adapters in $LAMARK_HOME/adapters/<name>/adapter_config.json
# and emits --enable-lora --lora-modules <name>=<container_path>... — so we
# just have to bounce the container and the fresh adapter is loaded.
log "Restarting vLLM via \`lamark serve start\` (auto-loads new adapter)..."
"$REPO/scripts/cmd/serve.sh" start >> "$LOG" 2>&1 || true
# Warm-up: model load + CUDA graph compile takes 3-7 min on Spark.
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
    if curl -fsS -m 3 "$VLLM_BASE_URL/models" >/dev/null 2>&1; then
        log "vLLM ready after ${i}0s"
        break
    fi
    sleep 30
done

# --- 4. Eval-gate ----------------------------------------------------------
log "Running eval-gate against $ADAPTER_NAME..."
GATE_RESULT="rejected"
if PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" \
       -m lamark.train.eval_gate \
       --adapter-name "$ADAPTER_NAME" \
       --base-url "$VLLM_BASE_URL" >> "$LOG" 2>&1
then
    GATE_RESULT="promoted"
    log "PASS: gate accepted $ADAPTER_NAME — promoting to default."
    # Update HERMES_HOME config.yaml to point default → new adapter.
    HERMES_HOME_CFG="$LAMARK_HOME/hermes-home/config.yaml"
    if [ -f "$HERMES_HOME_CFG" ]; then
        sed -i "s|^  default:.*|  default: $ADAPTER_NAME|" "$HERMES_HOME_CFG" >> "$LOG" 2>&1 || true
        log "Default model alias bumped to $ADAPTER_NAME in $HERMES_HOME_CFG"
    fi
    notify_success "$N_PAIRS" "$ADAPTER_NAME"
else
    log "FAIL: gate rejected $ADAPTER_NAME — previous default stays."
    notify_rejection "$N_PAIRS" "$ADAPTER_NAME"
fi

# --- 5. Record run outcome to train-history.jsonl --------------------------
# One line per terminal state (skipped/promoted/rejected) so `lamark train
# --status` and the Telegram notifier can read the latest result without
# re-running the gate. Skipped runs are recorded earlier (before training).
"$LAMARK_HOME/venv/bin/python" - <<PY >> "$LOG" 2>&1 || true
import json, os
record = {
    "ts": "$TS",
    "action": "$GATE_RESULT",
    "n_pairs": int("$N_PAIRS"),
    "adapter_name": "$ADAPTER_NAME",
    "adapter_path": "$ADAPTER_DIR/$ADAPTER_NAME",
}
with open("$LAMARK_HOME/train-history.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
print(f"history written: {record}")
PY

log "=== Lamark nightly retrain complete ($GATE_RESULT) ==="
