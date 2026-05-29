#!/usr/bin/env bash
# DGX Spark — Tier-1 SFT LoRA (plan/10) inside the lamark/sft image.
#
# Runs train_sft.py (TRL SFTTrainer) on a local HF checkpoint. Uses current
# transformers (5.x) so new architectures like Qwen3.5 load. This is the
# plan's primary nightly training tier; the older 03_train_lora.sh is the
# NemotronH/transformers-4.x path and is kept only for that base.
#
# Usage:
#   ./06_train_sft.sh
#   MODEL_ID=Qwen/Qwen3.5-9B-Base ./06_train_sft.sh
#   EPOCHS=1 ./06_train_sft.sh                 # quick run
#   MAX_STEPS=10 ./06_train_sft.sh             # smoke test
#
# Env vars (all have defaults):
#   SFT_IMAGE        training container          (default: lamark/sft:26.01)
#   MODEL_ID         HF repo id (-> on-host slug)(default: Qwen/Qwen3.5-9B-Base)
#   LAMARK_MODEL_DIR host model root             (default: ~/.lamark/models)
#   LAMARK_DATA_DIR  host data dir w/ train+val  (default: ~/.lamark/data)
#   OUTPUT_NAME      checkpoint subdir name      (default: <slug>-lora)
#   EPOCHS / MAX_STEPS / LORA_R / LORA_ALPHA / LR / MAX_LENGTH / ASSISTANT_ONLY
#                    forwarded to train_sft.py   (see its header for defaults)

set -euo pipefail

SFT_IMAGE="${SFT_IMAGE:-lamark/sft:26.01}"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3.5-9B-Base}"
MODEL_SLUG="$(printf '%s' "$MODEL_ID" | tr '/' '_')"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$HOME/.lamark/models}"
LAMARK_DATA_DIR="${LAMARK_DATA_DIR:-$HOME/.lamark/data}"
OUTPUT_NAME="${OUTPUT_NAME:-${MODEL_SLUG}-lora}"
CHECKPOINT_DIR="$HOME/.lamark/checkpoints/$OUTPUT_NAME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_LEARNING_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"  # learning/
MODEL_LOCAL="$LAMARK_MODEL_DIR/hf/$MODEL_SLUG"

log() { printf "\033[1;34m[sft]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[sft]\033[0m %s\n" "$*" >&2; }

log "image      : $SFT_IMAGE"
log "model      : $MODEL_ID  ($MODEL_LOCAL)"
log "data       : $LAMARK_DATA_DIR/{train,val}.jsonl"
log "checkpoint : $CHECKPOINT_DIR"

if [ ! -d "$MODEL_LOCAL" ]; then
    err "model not found: $MODEL_LOCAL — download it first (01_setup.sh or snapshot_download)"
    exit 1
fi
if [ ! -f "$LAMARK_DATA_DIR/train.jsonl" ] || [ ! -f "$LAMARK_DATA_DIR/val.jsonl" ]; then
    err "need $LAMARK_DATA_DIR/train.jsonl and val.jsonl"
    exit 1
fi
mkdir -p "$CHECKPOINT_DIR" "$HOME/.cache/huggingface"

# Forward only the train_sft.py knobs that were explicitly set in the env, so
# the script's own defaults apply otherwise.
ENV_ARGS=()
for v in EPOCHS MAX_STEPS LORA_R LORA_ALPHA LR MAX_LENGTH ASSISTANT_ONLY; do
    [ -n "${!v:-}" ] && ENV_ARGS+=(-e "$v=${!v}")
done

docker run --rm --gpus all --network host --shm-size 16g \
    -v "$REPO_LEARNING_DIR:/workspace/lamark" \
    -v "$MODEL_LOCAL:/model" \
    -v "$LAMARK_DATA_DIR:/data" \
    -v "$HOME/.lamark/checkpoints:/ckpt" \
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    -e MODEL_LOCAL=/model \
    -e DATA_TRAIN=/data/train.jsonl \
    -e DATA_VAL=/data/val.jsonl \
    -e OUTPUT_DIR="/ckpt/$OUTPUT_NAME" \
    -e HF_HUB_ENABLE_HF_TRANSFER=1 \
    "${ENV_ARGS[@]}" \
    "$SFT_IMAGE" \
    python /workspace/lamark/scripts/spark/train_sft.py

log "=== SFT complete ==="
log "adapter: $CHECKPOINT_DIR"
