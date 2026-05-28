#!/usr/bin/env bash
# DGX Spark — select training container, optionally clone NeMo examples, download model.
#
# Prefers the official NeMo NGC container but falls back to the NVIDIA PyTorch
# container (nvcr.io/nvidia/pytorch:26.01-py3) which ships pre-installed on most
# DGX Spark builds and requires no NGC authentication.  NeMo repo clone is
# attempted only when GitHub is reachable; skipped silently otherwise.
#
# Usage:
#   ./01_setup.sh                     # full setup
#   ./01_setup.sh --skip-model        # container selection + examples only
#   ./01_setup.sh --force             # re-pull image, re-clone repo, re-download
#   MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 ./01_setup.sh
#
# Env vars (all have defaults):
#   NEMO_IMAGE         NGC NeMo container (tried first; needs NGC auth)
#   PYTORCH_IMAGE      Fallback PyTorch container (no NGC auth required)
#   NEMO_REPO_DIR      Local NeMo clone path (optional; needs GitHub)
#   MODEL_ID           HuggingFace model repo
#   LAMARK_MODEL_DIR   Model storage root
#   HF_TOKEN           HuggingFace token for gated models (optional)

set -euo pipefail

NEMO_IMAGE="${NEMO_IMAGE:-nvcr.io/nvidia/nemo:latest}"
PYTORCH_IMAGE="${PYTORCH_IMAGE:-nvcr.io/nvidia/pytorch:26.01-py3}"
NEMO_REPO_DIR="${NEMO_REPO_DIR:-$HOME/nemo-framework}"
# Nemotron-Nano-Omni — verify the exact HF slug at hf.co/nvidia before running.
MODEL_ID="${MODEL_ID:-nvidia/Nemotron-Nano-Omni-3B}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$HOME/.lamark/models}"
HF_TOKEN="${HF_TOKEN:-}"
SKIP_MODEL=0
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --skip-model) SKIP_MODEL=1 ;;
        --force)      FORCE=1 ;;
        -h|--help)
            sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[setup]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[setup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup]\033[0m %s\n" "$*"; }

echo
log "=== DGX Spark NeMo training setup ==="
log "NeMo image : $NEMO_IMAGE"
log "PyTorch FB : $PYTORCH_IMAGE"
log "Model      : $MODEL_ID"
log "Model dir  : $LAMARK_MODEL_DIR"
echo

# 1. Pull training container (NeMo preferred; falls back to PyTorch if NGC auth fails)
ACTIVE_IMAGE=""
if docker image inspect "$NEMO_IMAGE" >/dev/null 2>&1 && [ "$FORCE" -eq 0 ]; then
    ok "NeMo image already present: $NEMO_IMAGE"
    ACTIVE_IMAGE="$NEMO_IMAGE"
elif docker image inspect "$PYTORCH_IMAGE" >/dev/null 2>&1 && [ "$FORCE" -eq 0 ]; then
    ok "PyTorch image already present (using fallback): $PYTORCH_IMAGE"
    ACTIVE_IMAGE="$PYTORCH_IMAGE"
else
    log "Pulling $NEMO_IMAGE (may take several minutes)..."
    if docker pull "$NEMO_IMAGE" 2>/dev/null; then
        ok "NeMo image ready."
        ACTIVE_IMAGE="$NEMO_IMAGE"
    else
        warn "NGC pull failed (needs NGC auth) — falling back to $PYTORCH_IMAGE"
        if docker image inspect "$PYTORCH_IMAGE" >/dev/null 2>&1; then
            ok "PyTorch fallback image already present."
        else
            log "Pulling $PYTORCH_IMAGE ..."
            docker pull "$PYTORCH_IMAGE"
            ok "PyTorch image ready."
        fi
        ACTIVE_IMAGE="$PYTORCH_IMAGE"
    fi
fi

# 2. Clone or update NeMo repo for reference examples (skip if GitHub unreachable)
if curl -s --max-time 5 https://github.com >/dev/null 2>&1; then
    if [ -d "$NEMO_REPO_DIR/.git" ] && [ "$FORCE" -eq 0 ]; then
        log "Updating NeMo repo at $NEMO_REPO_DIR..."
        (cd "$NEMO_REPO_DIR" && git pull --ff-only 2>/dev/null || warn "git pull skipped (local changes present)")
        ok "NeMo repo up to date."
    else
        [ "$FORCE" -eq 1 ] && rm -rf "$NEMO_REPO_DIR"
        log "Cloning NeMo repo to $NEMO_REPO_DIR..."
        git clone --depth 1 https://github.com/NVIDIA/NeMo "$NEMO_REPO_DIR"
        ok "NeMo repo cloned."
    fi
    log "NeMo LLM examples: $NEMO_REPO_DIR/examples/llm/"
else
    warn "GitHub unreachable — skipping NeMo repo clone (offline environment)."
fi

# 3. Download model
if [ "$SKIP_MODEL" -eq 1 ]; then
    warn "--skip-model: skipping model download."
else
    MODEL_SLUG=$(echo "$MODEL_ID" | tr '/' '_')
    LOCAL_DIR="$LAMARK_MODEL_DIR/hf/$MODEL_SLUG"
    mkdir -p "$LAMARK_MODEL_DIR/hf"

    if [ -d "$LOCAL_DIR" ] && [ "$FORCE" -eq 0 ]; then
        ok "Model already present: $LOCAL_DIR"
    else
        log "Downloading $MODEL_ID (may take 10-30 min)..."
        HF_TOKEN_ARG=""
        if [ -n "$HF_TOKEN" ]; then
            HF_TOKEN_ARG="-e HF_TOKEN=$HF_TOKEN"
        fi
        # shellcheck disable=SC2086
        docker run --rm \
            -e HF_HOME=/workspace/.cache/huggingface \
            -e HF_HUB_ENABLE_HF_TRANSFER=1 \
            -e _MODEL_ID="$MODEL_ID" \
            -e _LOCAL_DIR="/workspace/models/hf/$MODEL_SLUG" \
            $HF_TOKEN_ARG \
            -v "$LAMARK_MODEL_DIR:/workspace/models" \
            "$NEMO_IMAGE" \
            python3 -c "
import os
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id=os.environ['_MODEL_ID'],
    local_dir=os.environ['_LOCAL_DIR'],
    max_workers=8,
    token=os.environ.get('HF_TOKEN') or None,
)
print('downloaded', os.environ['_MODEL_ID'])
"
        ok "Model downloaded: $LOCAL_DIR"
    fi
fi

echo
ok "Setup complete."
echo "  Next: ./02_prepare_data.sh"
echo "  Then: ./03_train_lora.sh"
