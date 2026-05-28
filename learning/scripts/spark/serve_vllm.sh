#!/usr/bin/env bash
# Start vLLM as a persistent OpenAI-compatible server with the Lamark adapter.
#
# Usage:
#   ./serve_vllm.sh start    # start the server (default)
#   ./serve_vllm.sh stop     # stop and remove
#   ./serve_vllm.sh logs     # tail logs
#   ./serve_vllm.sh status   # show container state
#
# The server listens on $PORT (default 8765) and exposes two model names:
#   base    -> the raw NemotronH base model
#   lamark  -> the base model + the LoRA adapter at $ADAPTER_DIR
#
# Example client call:
#   curl http://10.212.212.1:8765/v1/chat/completions \
#       -H 'Content-Type: application/json' \
#       -d '{"model":"lamark","messages":[{"role":"user","content":"hi"}]}'

set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/.lamark/models/hf/nvidia_NVIDIA-Nemotron-3-Nano-4B-BF16}"
ADAPTER_DIR="${ADAPTER_DIR:-$HOME/.lamark/checkpoints/nemotron-nano-lora/checkpoint-600}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.21.0}"
PORT="${PORT:-8765}"
CONTAINER="${CONTAINER:-lamark-vllm-server}"
MAX_LORA_RANK="${MAX_LORA_RANK:-64}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"

log() { printf "\033[1;34m[vllm]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[vllm]\033[0m %s\n" "$*"; }

cmd="${1:-start}"
case "$cmd" in

  start)
    if docker ps --format '{{.Names}}' | grep -q "^$CONTAINER\$"; then
        ok "Already running. URL: http://localhost:$PORT/v1"
        exit 0
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    log "Starting $VLLM_IMAGE on port $PORT ..."
    log "  Model   : $MODEL_DIR        served as 'base'"
    log "  Adapter : $ADAPTER_DIR  served as 'lamark'"

    docker run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        --gpus all \
        --shm-size 8g \
        -p "$PORT:8000" \
        -v "$MODEL_DIR:/model" \
        -v "$ADAPTER_DIR:/adapter" \
        "$VLLM_IMAGE" \
        /model \
        --served-model-name base \
        --enable-lora \
        --max-lora-rank "$MAX_LORA_RANK" \
        --lora-modules lamark=/adapter \
        --trust-remote-code \
        --max-model-len "$MAX_MODEL_LEN" \
        --dtype bfloat16 \
        --gpu-memory-utilization 0.85 \
        >/dev/null

    log "Waiting for /health ..."
    for i in $(seq 1 120); do
        if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
            ok "Ready after ${i}s. URL: http://localhost:$PORT/v1"
            ok "Models served: base, lamark"
            exit 0
        fi
        if ! docker ps --format '{{.Names}}' | grep -q "^$CONTAINER\$"; then
            log "Container died early. Last logs:"
            docker logs "$CONTAINER" 2>&1 | tail -30
            exit 1
        fi
        sleep 2
    done
    log "Timed out waiting for vLLM. Last logs:"
    docker logs "$CONTAINER" 2>&1 | tail -30
    exit 1
    ;;

  stop)
    log "Stopping $CONTAINER ..."
    docker stop "$CONTAINER" >/dev/null 2>&1 || true
    docker rm   "$CONTAINER" >/dev/null 2>&1 || true
    ok "Stopped."
    ;;

  logs)
    docker logs --tail 100 -f "$CONTAINER"
    ;;

  status)
    docker ps -a --filter "name=$CONTAINER" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;

  *)
    echo "usage: $0 {start|stop|logs|status}" >&2
    exit 2
    ;;
esac
