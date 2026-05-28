#!/usr/bin/env bash
# Probe NemotronH + LoRA adapter via vLLM's OpenAI-compatible HTTP API.
# Starts a temporary vLLM container, waits for /health, runs prompts, tears down.

set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/.lamark/models/hf/nvidia_NVIDIA-Nemotron-3-Nano-4B-BF16}"
ADAPTER_DIR="${ADAPTER_DIR:-$HOME/.lamark/checkpoints/nemotron-nano-lora/checkpoint-60}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.21.0}"
PORT="${PORT:-8765}"
CONTAINER="${CONTAINER:-lamark-vllm-probe}"

log() { printf "\033[1;34m[vllm-probe]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[vllm-probe]\033[0m %s\n" "$*"; }

cleanup() {
    log "Stopping vLLM container ..."
    docker stop "$CONTAINER" >/dev/null 2>&1 || true
    docker rm   "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Ensure no stale container
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

log "Starting vLLM ($VLLM_IMAGE) on port $PORT ..."
log "  Model   : $MODEL_DIR"
log "  Adapter : $ADAPTER_DIR  (served as model name 'lamark')"

docker run -d \
    --name "$CONTAINER" \
    --gpus all \
    --shm-size 8g \
    -p "$PORT:8000" \
    -v "$MODEL_DIR:/model" \
    -v "$ADAPTER_DIR:/adapter" \
    "$VLLM_IMAGE" \
    /model \
    --served-model-name base \
    --enable-lora \
    --lora-modules lamark=/adapter \
    --trust-remote-code \
    --max-model-len 4096 \
    --dtype bfloat16 \
    --gpu-memory-utilization 0.85 \
    >/dev/null

log "Waiting for vLLM /health ..."
for i in $(seq 1 120); do
    if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
        ok "vLLM ready after ${i}s."
        break
    fi
    if ! docker ps --format '{{.Names}}' | grep -q "^$CONTAINER\$"; then
        log "Container died early. Last logs:"
        docker logs "$CONTAINER" 2>&1 | tail -40
        exit 1
    fi
    sleep 2
done

if ! curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
    log "Timed out waiting for vLLM. Last logs:"
    docker logs "$CONTAINER" 2>&1 | tail -40
    exit 1
fi

log "Listing models:"
curl -s "http://localhost:$PORT/v1/models" | python3 -m json.tool

PROMPTS=(
    "What is Lamark?"
    "Explain the SQ/EQ submission/event queue pattern in Lamark."
    "What are the four memory layers in Lamark?"
    "Which Rust crate in Lamark owns the ModelProvider trait?"
    "Why is bitsandbytes QLoRA not used for MoE training on DGX Spark?"
    "What is the structure of a Lamark trace bundle?"
    "What does the Curator background agent do?"
    "How does Lamark integrate with the knowledge-base service?"
)

query() {
    local model="$1" prompt="$2"
    curl -s -X POST "http://localhost:$PORT/v1/chat/completions" \
        -H 'Content-Type: application/json' \
        -d "$(python3 -c "
import json, sys
print(json.dumps({
    'model': '$model',
    'messages': [{'role': 'user', 'content': sys.argv[1]}],
    'max_tokens': 200,
    'temperature': 0.0,
    'chat_template_kwargs': {'enable_thinking': False},
}))
" "$prompt")" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'].strip() if 'choices' in d else d)"
}

echo
echo "============================================================================"
for i in "${!PROMPTS[@]}"; do
    n=$((i+1))
    p="${PROMPTS[$i]}"
    echo
    echo "[Q$n] $p"
    echo "[BASE   ] $(query base    "$p")"
    echo "[ADAPTER] $(query lamark  "$p")"
    echo "----------------------------------------------------------------------------"
done

ok "Probe complete."
