#!/usr/bin/env bash
# Persistent OpenAI-compatible chat server for a Lamark SFT adapter, backed by
# transformers (serve_chat.py) in the lamark/sft image. Use this instead of
# serve_vllm.sh when the base arch is too new for the vLLM image (e.g. qwen3_5).
#
# Listens on $PORT (default 8765, mapped to container 8000) so the local
# chat_lamark.py REPL connects with no changes. Serves model names base + lamark.
#
# Usage:
#   ./serve_chat.sh start | stop | logs | status
#
# Env:
#   MODEL_ID     HF repo id -> on-host slug   (default: Qwen/Qwen3.5-9B)
#   ADAPTER_NAME checkpoint subdir name       (default: qwen3_5-9b-instruct-lora)
#   SFT_IMAGE    container                    (default: lamark/sft:26.01)
#   PORT         host port                    (default: 8765)

set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen3.5-9B}"
MODEL_SLUG="$(printf '%s' "$MODEL_ID" | tr '/' '_')"
ADAPTER_NAME="${ADAPTER_NAME:-qwen3_5-9b-instruct-lora}"
SFT_IMAGE="${SFT_IMAGE:-lamark/sft:26.01}"
PORT="${PORT:-8765}"
CONTAINER="${CONTAINER:-lamark-chat-server}"

MODEL_LOCAL="$HOME/.lamark/models/hf/$MODEL_SLUG"
ADAPTER_DIR="$HOME/.lamark/checkpoints/$ADAPTER_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_LEARNING_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

log() { printf "\033[1;34m[chat]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[chat]\033[0m %s\n" "$*"; }

case "${1:-start}" in
  start)
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    [ -d "$MODEL_LOCAL" ] || { echo "model not found: $MODEL_LOCAL" >&2; exit 1; }
    [ -d "$ADAPTER_DIR" ] || { echo "adapter not found: $ADAPTER_DIR" >&2; exit 1; }
    log "starting $CONTAINER on :$PORT  (model $MODEL_ID + $ADAPTER_NAME)"
    docker run -d --name "$CONTAINER" --restart unless-stopped \
        --gpus all --shm-size 16g \
        -p "$PORT:8000" \
        -v "$REPO_LEARNING_DIR:/workspace/lamark" \
        -v "$MODEL_LOCAL:/model" \
        -v "$ADAPTER_DIR:/adapter" \
        -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
        -e MODEL_LOCAL=/model -e ADAPTER_DIR=/adapter -e PORT=8000 \
        "$SFT_IMAGE" \
        python /workspace/lamark/scripts/spark/serve_chat.py >/dev/null

    log "waiting for /health (model load ~5 min) ..."
    for i in $(seq 1 300); do
        if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
            ok "ready: http://localhost:$PORT/v1   (models: base, lamark)"
            exit 0
        fi
        if ! docker ps --format '{{.Names}}' | grep -q "^$CONTAINER\$"; then
            echo "container died; last logs:" >&2; docker logs "$CONTAINER" 2>&1 | tail -30; exit 1
        fi
        sleep 2
    done
    echo "timed out; last logs:" >&2; docker logs "$CONTAINER" 2>&1 | tail -30; exit 1
    ;;
  stop)   docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; ok "stopped." ;;
  logs)   docker logs --tail 100 -f "$CONTAINER" ;;
  status) docker ps -a --filter "name=$CONTAINER" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  *) echo "usage: $0 {start|stop|logs|status}" >&2; exit 2 ;;
esac
