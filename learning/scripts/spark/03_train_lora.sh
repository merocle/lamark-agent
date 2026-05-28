#!/usr/bin/env bash
# DGX Spark — run NeMo LoRA SFT inside the NeMo container.
#
# Steps performed:
#   1. Prepare data (runs 02_prepare_data.py inside container)
#   2. Launch NeMo LoRA SFT training via train_lora.py
#   3. Print checkpoint path on success
#
# Usage:
#   ./03_train_lora.sh
#   ./03_train_lora.sh --skip-data        # skip data prep (reuse existing)
#   ./03_train_lora.sh --steps 50         # quick smoke test (50 steps)
#
# Env vars:
#   TRAIN_IMAGE       training container   (default: nvcr.io/nvidia/pytorch:26.01-py3)
#   MODEL_ID          HF model repo        (default: nvidia/Nemotron-Nano-Omni-3B)
#   LAMARK_MODEL_DIR  host model dir       (default: ~/.lamark/models)
#   LAMARK_DATA_DIR   host data dir        (default: ~/.lamark/data)
#   MAX_STEPS         training steps       (default: 200)

set -euo pipefail

TRAIN_IMAGE="${TRAIN_IMAGE:-nvcr.io/nvidia/pytorch:26.01-py3}"
MODEL_ID="${MODEL_ID:-nvidia/Nemotron-Nano-Omni-3B}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$HOME/.lamark/models}"
LAMARK_DATA_DIR="${LAMARK_DATA_DIR:-$HOME/.lamark/data}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-$HOME/.lamark/checkpoints/nemotron-nano-lora}"
MAX_STEPS="${MAX_STEPS:-200}"
SKIP_DATA=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

for arg in "$@"; do
    case "$arg" in
        --skip-data) SKIP_DATA=1 ;;
        --steps)     shift; MAX_STEPS="$1" ;;
        -h|--help)
            sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[train]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[train]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[train]\033[0m %s\n" "$*"; }

echo
log "=== NeMo LoRA SFT on DGX Spark ==="
log "Container  : $TRAIN_IMAGE"
log "Model      : $MODEL_ID"
log "Max steps  : $MAX_STEPS"
log "Checkpoint : $CHECKPOINT_DIR"
echo

mkdir -p "$CHECKPOINT_DIR" "$LAMARK_DATA_DIR"

MODEL_SLUG=$(echo "$MODEL_ID" | tr '/' '_')
MODEL_LOCAL="$LAMARK_MODEL_DIR/hf/$MODEL_SLUG"
if [ ! -d "$MODEL_LOCAL" ]; then
    echo "ERROR: model not found at $MODEL_LOCAL. Run 01_setup.sh first." >&2
    exit 2
fi

DOCKER_RUN=(
    docker run --rm
    --gpus all
    --network host
    --shm-size 16g
    --ulimit memlock=-1
    --ulimit stack=67108864
    -e TORCH_CUDA_ARCH_LIST=12.1
    -e HF_HOME=/workspace/.cache/huggingface
    -e HF_HUB_ENABLE_HF_TRANSFER=1
    -e MODEL_ID="$MODEL_ID"
    -e MODEL_LOCAL="/workspace/models/hf/$MODEL_SLUG"
    -e MAX_STEPS="$MAX_STEPS"
    -e DATA_TRAIN=/workspace/data/train.jsonl
    -e DATA_VAL=/workspace/data/val.jsonl
    -e CHECKPOINT_DIR=/workspace/checkpoints
    -v "$LAMARK_MODEL_DIR:/workspace/models"
    -v "$LAMARK_DATA_DIR:/workspace/data"
    -v "$CHECKPOINT_DIR:/workspace/checkpoints"
    -v "$SCRIPT_DIR:/workspace/scripts"
    -v "$REPO_ROOT:/workspace/lamark-agent"
    -w /workspace
    "$TRAIN_IMAGE"
)

# 1. Prepare data
if [ "$SKIP_DATA" -eq 0 ]; then
    log "Preparing dataset inside container..."
    "${DOCKER_RUN[@]}" bash -c "
        pip install datasets -q
        python3 /workspace/scripts/02_prepare_data.py --total 1000 --out-dir /workspace/data
    "
    ok "Dataset ready."
fi

# 2. Run LoRA training
log "Starting LoRA SFT training ($MAX_STEPS steps)..."
"${DOCKER_RUN[@]}" bash -c "
    pip install peft trl accelerate 'torchao>=0.16.0' -q
    pip install --no-build-isolation mamba-ssm causal-conv1d -q
    python3 /workspace/scripts/train_lora.py
"

ok "Training complete."
echo
log "Checkpoint saved to: $CHECKPOINT_DIR"
echo "  Next: ./04_validate.sh"
