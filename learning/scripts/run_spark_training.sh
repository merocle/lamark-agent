#!/usr/bin/env bash
# Local orchestrator — run the full Nemotron Nano LoRA training sequence
# on the DGX Spark over SSH.
#
# Steps:
#   0. spark: 00_cleanup.sh   — stop containers, prune Docker
#   1. spark: 01_setup.sh     — pull NeMo image, clone NeMo repo, download model
#   2. spark: 03_train_lora.sh — prepare data + run LoRA SFT (NeMo 2.0)
#   3. spark: 04_validate.sh  — perplexity + sample generations
#
# Usage (from your local machine):
#   ./scripts/run_spark_training.sh
#   ./scripts/run_spark_training.sh --steps 50        # quick smoke test
#   ./scripts/run_spark_training.sh --skip-cleanup    # skip step 0
#   ./scripts/run_spark_training.sh --skip-setup      # skip step 1
#   SPARK_HOST=jetbrains@10.212.212.1 ./scripts/run_spark_training.sh
#
# Requirements (local machine):
#   - ssh key access to SPARK_HOST (or SSH_KEY set to a key file)
#   - The lamark-agent repo must be present on Spark at SPARK_REPO_DIR
#
# Requirements (DGX Spark, auto-satisfied by 01_setup.sh):
#   - Docker with NVIDIA container toolkit
#   - nvidia-smi accessible
#   - ~50 GB free on model storage path

set -euo pipefail

SPARK_HOST="${SPARK_HOST:-jetbrains@10.212.212.1}"
SPARK_REPO_DIR="${SPARK_REPO_DIR:-\$HOME/lamark-agent}"
SSH_KEY="${SSH_KEY:-}"
MAX_STEPS="${MAX_STEPS:-200}"
SKIP_CLEANUP=0
SKIP_SETUP=0

for arg in "$@"; do
    case "$arg" in
        --skip-cleanup) SKIP_CLEANUP=1 ;;
        --skip-setup)   SKIP_SETUP=1 ;;
        --steps)        shift; MAX_STEPS="$1" ;;
        -h|--help)
            sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
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

# Sync the spark/ scripts to Spark (rsync is faster than git pull for quick iteration)
log "Syncing learning/scripts/spark/ to $SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/spark/ ..."
rsync -az --progress \
    --rsh "ssh ${SSH_OPTS[*]}" \
    "$(dirname "$0")/spark/" \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/spark/"
ok "Scripts synced."
echo

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
    spark_run "Step 1/4: pull NeMo container + download model" bash -c "
        chmod +x $SPARK_SCRIPTS/01_setup.sh
        $SPARK_SCRIPTS/01_setup.sh
    "
    echo
else
    log "Step 1/4: setup skipped (--skip-setup)."
fi

# ── Step 2: LoRA training ─────────────────────────────────────────────────────
spark_run "Step 2/4: LoRA SFT training (max_steps=$MAX_STEPS)" bash -c "
    chmod +x $SPARK_SCRIPTS/03_train_lora.sh
    MAX_STEPS=$MAX_STEPS $SPARK_SCRIPTS/03_train_lora.sh
"
echo

# ── Step 3: validation ────────────────────────────────────────────────────────
spark_run "Step 3/4: validate adapter" bash -c "
    chmod +x $SPARK_SCRIPTS/04_validate.sh
    $SPARK_SCRIPTS/04_validate.sh
"
echo

ok "=== Training sequence complete ==="
echo
echo "  Spark checkpoint: $SPARK_HOST:~/.lamark/checkpoints/nemotron-nano-lora/"
echo
echo "  To copy adapter back locally:"
echo "    rsync -az $SPARK_HOST:~/.lamark/checkpoints/nemotron-nano-lora/ \\"
echo "          ~/.lamark/adapters/nemotron-nano-lora/"
