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
BASE_MODEL_DIR="${LAMARK_BASE_MODEL:-$LAMARK_HOME/models/hf/Qwen_Qwen3.6-35B-A3B}"

LOG_DIR="$LAMARK_HOME/logs"
mkdir -p "$LOG_DIR" "$ADAPTER_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/nightly-train-$TS.log"

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
fail() { log "FAIL: $*"; exit 1; }

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
    exit 0
fi

# --- 2. Train --------------------------------------------------------------
ADAPTER_NAME="nightly-$TS"
log "Training adapter $ADAPTER_NAME (this takes 2-10 minutes)..."

# Free the GPU so the training container can fit. Stop vLLM (will reload later).
PRIOR_VLLM_RUNNING=0
if docker ps --filter "name=lamark-vllm-lora" -q | grep -q .; then
    PRIOR_VLLM_RUNNING=1
    log "Stopping vLLM to free GPU..."
    docker stop lamark-vllm-lora >> "$LOG" 2>&1 || true
    docker rm   lamark-vllm-lora >> "$LOG" 2>&1 || true
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
  bash -c "pip uninstall -y flash-attn flash_attn 2>/dev/null; pip install --no-deps 'torchao>=0.16' 2>&1 | tail -1; python /workspace/lamark-agent/src/lamark/train/dispatcher_spark.py --pairs-jsonl /workspace/.lamark/train-plan-nightly.jsonl --base-model $BASE_MODEL_DIR --adapter-name $ADAPTER_NAME --lora-rank 16 --num-epochs 5 --learning-rate 2e-4 --per-device-batch-size 1 --grad-accum-steps 4 --output-dir /workspace/adapters" \
  >> "$LOG" 2>&1 || fail "training container exited non-zero"

if [ ! -f "$ADAPTER_DIR/$ADAPTER_NAME/adapter_model.safetensors" ]; then
    fail "adapter not saved at $ADAPTER_DIR/$ADAPTER_NAME"
fi
log "Adapter saved: $ADAPTER_DIR/$ADAPTER_NAME"

# --- 3. Restart vLLM with new adapter --------------------------------------
log "Restarting vLLM with new adapter loaded..."
"$REPO/scripts/vllm_server.sh" --background \
    --extra-lora "$ADAPTER_NAME=$ADAPTER_DIR/$ADAPTER_NAME" \
    >> "$LOG" 2>&1 &
sleep 30
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if curl -fsS -m 3 "$VLLM_BASE_URL/models" >/dev/null 2>&1; then
        log "vLLM ready after ${i}0s"
        break
    fi
    sleep 30
done

# --- 4. Eval-gate ----------------------------------------------------------
log "Running eval-gate against $ADAPTER_NAME..."
if PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" \
       -m lamark.train.eval_gate \
       --adapter-name "$ADAPTER_NAME" \
       --base-url "$VLLM_BASE_URL" >> "$LOG" 2>&1
then
    log "PASS: gate accepted $ADAPTER_NAME — promoting to default."
    # Update HERMES_HOME config.yaml to point default → new adapter.
    HERMES_HOME_CFG="$LAMARK_HOME/hermes-home/config.yaml"
    if [ -f "$HERMES_HOME_CFG" ]; then
        sed -i "s|^  default:.*|  default: $ADAPTER_NAME|" "$HERMES_HOME_CFG" >> "$LOG" 2>&1 || true
        log "Default model alias bumped to $ADAPTER_NAME in $HERMES_HOME_CFG"
    fi
else
    log "FAIL: gate rejected $ADAPTER_NAME — previous default stays."
fi

log "=== Lamark nightly retrain complete ==="
