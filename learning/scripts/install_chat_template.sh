#!/usr/bin/env bash
# Install the Lamark chat template into a downloaded model directory.
#
# vLLM (and HF transformers' apply_chat_template) reads chat_template.jinja
# from the model dir. Overwriting that file is how L1 — the default Lamark
# identity system prompt — is delivered. See docs/decisions/0011-...
#
# The original template is backed up to chat_template.jinja.orig (only on
# the first install; subsequent runs do not clobber the backup).
#
# Usage:
#   ./install_chat_template.sh [MODEL_DIR]
#
# MODEL_DIR defaults to the production 30B model path on Spark. Set it
# explicitly when installing into a different model or a non-default
# location.
#
# Idempotent: re-running with no template changes is a no-op.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_SRC="$REPO_ROOT/learning/templates/lamark_chat_template.jinja"

MODEL_DIR="${1:-$HOME/.lamark/models/hf/nvidia_NVIDIA-Nemotron-3-Nano-30B-A3B-BF16}"
TARGET="$MODEL_DIR/chat_template.jinja"
BACKUP="$MODEL_DIR/chat_template.jinja.orig"

log() { printf "\033[1;34m[chat-tmpl]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[chat-tmpl]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[chat-tmpl]\033[0m %s\n" "$*" >&2; }

if [ ! -f "$TEMPLATE_SRC" ]; then
    err "Source template missing: $TEMPLATE_SRC"
    exit 1
fi
if [ ! -d "$MODEL_DIR" ]; then
    err "Model dir not found: $MODEL_DIR"
    err "Download the model first (lamark setup picks the default for your hardware tier)."
    exit 1
fi

# First-time backup of whatever shipped with the model.
if [ -f "$TARGET" ] && [ ! -f "$BACKUP" ]; then
    log "Backing up vendor template: $BACKUP"
    cp "$TARGET" "$BACKUP"
fi

if [ -f "$TARGET" ] && cmp -s "$TEMPLATE_SRC" "$TARGET"; then
    ok "Already up to date: $TARGET"
    exit 0
fi

log "Installing Lamark template:"
log "  src   : $TEMPLATE_SRC"
log "  dst   : $TARGET"
cp "$TEMPLATE_SRC" "$TARGET"
ok "Installed. vLLM / transformers will use the Lamark identity by default."
log "Restart vLLM (serve_vllm.sh stop && serve_vllm.sh start) to pick it up."
