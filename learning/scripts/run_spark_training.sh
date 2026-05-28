#!/usr/bin/env bash
# Local orchestrator — run the full Nemotron Nano LoRA training sequence
# on the DGX Spark over SSH.
#
# Steps:
#   0. spark: 00_cleanup.sh    — stop containers, prune Docker
#   1. spark: 01_setup.sh      — pull training container, download model
#   2. spark: 03_train_lora.sh — prepare data + run LoRA SFT
#   3. spark: 04_validate.sh   — perplexity delta (base vs adapter)
#
# Usage (from your local machine):
#   ./scripts/run_spark_training.sh
#   ./scripts/run_spark_training.sh --steps 50        # quick smoke test
#   ./scripts/run_spark_training.sh --skip-cleanup    # skip step 0
#   ./scripts/run_spark_training.sh --skip-setup      # skip step 1
#   SPARK_HOST=jetbrains@10.212.212.1 ./scripts/run_spark_training.sh
#
# Requirements (local machine):
#   - ssh + scp access to SPARK_HOST (OpenSSH; no rsync needed)
#   - SSH_KEY set if not using the default key
#
# Requirements (DGX Spark):
#   - Docker with NVIDIA container toolkit
#   - nvcr.io/nvidia/pytorch:26.01-py3 image available (or set TRAIN_IMAGE)
#   - ~50 GB free on model storage path

set -euo pipefail

SPARK_HOST="${SPARK_HOST:-jetbrains@10.212.212.1}"
SPARK_REPO_DIR="${SPARK_REPO_DIR:-lamark-agent}"
SSH_KEY="${SSH_KEY:-}"
MAX_STEPS="${MAX_STEPS:-200}"
MODEL_ID="${MODEL_ID:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
SKIP_CLEANUP=0
SKIP_SETUP=0
SKIP_DATA=0
DATASET_DIR="${DATASET_DIR:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-cleanup) SKIP_CLEANUP=1; shift ;;
        --skip-setup)   SKIP_SETUP=1; shift ;;
        --skip-data)    SKIP_DATA=1; shift ;;
        --dataset)      shift; DATASET_DIR="$1"; SKIP_DATA=1; shift ;;
        --steps)        shift; MAX_STEPS="$1"; shift ;;
        -h|--help)
            sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[spark-run]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[spark-run]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[spark-run]\033[0m %s\n" "$*" >&2; }

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
if [ -n "$SSH_KEY" ]; then
    SSH_OPTS+=(-i "$SSH_KEY")
fi

spark_run() {
    local desc="$1"; shift
    log "$desc"
    ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "$@"
}

# Verify connectivity
log "Checking SSH connectivity to $SPARK_HOST ..."
if ! ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "echo ok" &>/dev/null; then
    err "Cannot reach $SPARK_HOST. Check SPARK_HOST and your SSH key."
    exit 2
fi
ok "SSH connection OK."
echo

# Sync scripts to Spark via scp (no rsync required; works on Windows OpenSSH)
SPARK_SCRIPT_DIR="$(dirname "$0")/spark"
log "Syncing learning/scripts/spark/ to $SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/spark/ ..."
ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "mkdir -p $SPARK_REPO_DIR/learning/scripts/spark"
scp "${SSH_OPTS[@]}" -r "$SPARK_SCRIPT_DIR/." \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/spark/"
ok "Scripts synced."
echo

# Sync custom dataset to Spark if --dataset was passed
if [ -n "$DATASET_DIR" ]; then
    if [ ! -f "$DATASET_DIR/train.jsonl" ] || [ ! -f "$DATASET_DIR/val.jsonl" ]; then
        err "Dataset dir must contain train.jsonl and val.jsonl: $DATASET_DIR"
        exit 2
    fi
    log "Syncing dataset $DATASET_DIR -> $SPARK_HOST:~/.lamark/data/ ..."
    ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "mkdir -p ~/.lamark/data"
    scp "${SSH_OPTS[@]}" "$DATASET_DIR/train.jsonl" "$DATASET_DIR/val.jsonl" \
        "$SPARK_HOST:.lamark/data/"
    ok "Dataset synced."
    echo
fi

SPARK_SCRIPTS="$SPARK_REPO_DIR/learning/scripts/spark"

# ── Step 0: cleanup ──────────────────────────────────────────────────────────
if [ "$SKIP_CLEANUP" -eq 0 ]; then
    spark_run "Step 0/4: cleanup DGX Spark" bash -c "
        chmod +x $SPARK_SCRIPTS/00_cleanup.sh
        $SPARK_SCRIPTS/00_cleanup.sh --yes
    "
    echo
else
    log "Step 0/4: cleanup skipped (--skip-cleanup)."
fi

# ── Step 1: setup (pull NeMo image + model) ──────────────────────────────────
if [ "$SKIP_SETUP" -eq 0 ]; then
    spark_run "Step 1/4: pull training container + download model" bash -c "
        chmod +x $SPARK_SCRIPTS/01_setup.sh
        $SPARK_SCRIPTS/01_setup.sh
    "
    echo
else
    log "Step 1/4: setup skipped (--skip-setup)."
fi

# ── Step 2: LoRA training ─────────────────────────────────────────────────────
TRAIN_FLAGS=""
[ "$SKIP_DATA" -eq 1 ] && TRAIN_FLAGS="--skip-data"

spark_run "Step 2/4: LoRA SFT training (max_steps=$MAX_STEPS, flags='$TRAIN_FLAGS')" bash -c "
    chmod +x $SPARK_SCRIPTS/03_train_lora.sh
    MAX_STEPS=$MAX_STEPS MODEL_ID='$MODEL_ID' $SPARK_SCRIPTS/03_train_lora.sh $TRAIN_FLAGS
"
echo

# ── Step 3: validation ────────────────────────────────────────────────────────
spark_run "Step 3/4: validate adapter" bash -c "
    chmod +x $SPARK_SCRIPTS/04_validate.sh
    MODEL_ID='$MODEL_ID' $SPARK_SCRIPTS/04_validate.sh
"
echo

ok "=== Training sequence complete ==="
echo
echo "  Spark checkpoint: $SPARK_HOST:~/.lamark/checkpoints/nemotron-nano-lora/"
echo
echo "  To copy adapter back locally (scp, no rsync needed):"
echo "    mkdir -p ~/.lamark/adapters/nemotron-nano-lora"
echo "    scp -r $SPARK_HOST:~/.lamark/checkpoints/nemotron-nano-lora/. \\"
echo "          ~/.lamark/adapters/nemotron-nano-lora/"
