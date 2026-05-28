#!/usr/bin/env bash
# DGX Spark — validate the LoRA adapter produced by 03_train_lora.sh.
#
# Loads the base model + adapter, runs on the validation set, prints
# perplexity and a few sample generations to confirm the adapter is
# functional before registering it with the model registry.
#
# Usage:
#   ./04_validate.sh
#   ./04_validate.sh --max-samples 50   # limit validation samples
#
# Env vars:
#   TRAIN_IMAGE       training container    (default: nvcr.io/nvidia/pytorch:26.01-py3)
#   MODEL_ID          HF model repo         (default: nvidia/Nemotron-Nano-Omni-3B)
#   LAMARK_MODEL_DIR  host model dir        (default: ~/.lamark/models)
#   LAMARK_DATA_DIR   host data dir         (default: ~/.lamark/data)
#   CHECKPOINT_DIR    adapter root dir      (default: ~/.lamark/checkpoints/nemotron-nano-lora)
#   CHECKPOINT_SUBDIR checkpoint subdir     (default: auto-detected, e.g. checkpoint-200)

set -euo pipefail

TRAIN_IMAGE="${TRAIN_IMAGE:-nvcr.io/nvidia/pytorch:26.01-py3}"
MODEL_ID="${MODEL_ID:-nvidia/Nemotron-Nano-Omni-3B}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$HOME/.lamark/models}"
LAMARK_DATA_DIR="${LAMARK_DATA_DIR:-$HOME/.lamark/data}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-$HOME/.lamark/checkpoints/nemotron-nano-lora}"
CHECKPOINT_SUBDIR="${CHECKPOINT_SUBDIR:-}"
MAX_SAMPLES="${MAX_SAMPLES:-50}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for arg in "$@"; do
    case "$arg" in
        --max-samples) shift; MAX_SAMPLES="$1" ;;
        -h|--help)
            sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[validate]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[validate]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[validate]\033[0m %s\n" "$*"; }

echo
log "=== LoRA adapter validation ==="
log "Container : $TRAIN_IMAGE"
log "Model     : $MODEL_ID"
log "Adapter   : $CHECKPOINT_DIR"
log "Max samp. : $MAX_SAMPLES"
echo

if [ ! -d "$CHECKPOINT_DIR" ]; then
    echo "ERROR: checkpoint not found at $CHECKPOINT_DIR. Run 03_train_lora.sh first." >&2
    exit 2
fi

MODEL_SLUG=$(echo "$MODEL_ID" | tr '/' '_')

# Find the latest checkpoint-* subdirectory (HF Trainer saves checkpoint-N)
if [ -z "$CHECKPOINT_SUBDIR" ]; then
    CHECKPOINT_SUBDIR=$(ls -1dt "$CHECKPOINT_DIR"/checkpoint-* 2>/dev/null \
        | head -1 | xargs -r basename) || true
    CHECKPOINT_SUBDIR="${CHECKPOINT_SUBDIR:-checkpoint-200}"
fi
log "Checkpoint subdir : $CHECKPOINT_SUBDIR"

docker run --rm \
    --gpus all \
    --network host \
    --shm-size 8g \
    -e TORCH_CUDA_ARCH_LIST=12.1 \
    -e HF_HOME=/workspace/.cache/huggingface \
    -e MODEL_LOCAL="/workspace/models/hf/$MODEL_SLUG" \
    -e ADAPTER_DIR="/workspace/checkpoints/$CHECKPOINT_SUBDIR" \
    -e DATA_VAL=/workspace/data/val.jsonl \
    -e MAX_SAMPLES="$MAX_SAMPLES" \
    -v "$LAMARK_MODEL_DIR:/workspace/models" \
    -v "$LAMARK_DATA_DIR:/workspace/data" \
    -v "$CHECKPOINT_DIR:/workspace/checkpoints" \
    -v "$SCRIPT_DIR:/workspace/scripts" \
    -w /workspace \
    "$TRAIN_IMAGE" \
    bash -c "
        pip install safetensors accelerate 'torchao>=0.16.0' -q
        pip install --no-build-isolation mamba-ssm causal-conv1d -q
        python3 /workspace/scripts/validate_final.py
    "

ok "Validation complete."
