#!/usr/bin/env bash
# DEPRECATED — not the supported serving path.
#
# Canonical serving is `lamark serve` (scripts/cmd/serve.sh), which runs the
# upstream multi-arch `vllm/vllm-openai:v0.21.0` image with hardware-gated
# flags. This script predates that: it serves via the Spark-native
# `lamark/vllm:25.10` (a TRAINING image) and has no hardware gating. Kept only
# as a manual fallback / smoke harness (scripts/smoke_test_via_server.py).
# Do not extend it — use serve.sh.
#
# Lamark — start vLLM serving Qwen3.6-35B-A3B inside the NGC container.
#
# Per feasibility-report-v3.md §4 and Kreuzhofer's recipe in the user-supplied
# research artifact. Single-stream target: 25-32 tok/s decode.
#
# Usage:
#   ./scripts/vllm_server.sh                 # foreground (logs to stdout)
#   ./scripts/vllm_server.sh --detach        # background as docker container
#   ./scripts/vllm_server.sh --stop          # stop the detached server
#   ./scripts/vllm_server.sh --logs          # tail logs from detached server

set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$LAMARK_HOME/models}"
CONTAINER_NAME="${LAMARK_VLLM_CONTAINER:-lamark-vllm}"
PRIMARY_MODEL="${LAMARK_PRIMARY_MODEL:-Qwen/Qwen3.6-35B-A3B}"
PRIMARY_QUANT="${LAMARK_PRIMARY_QUANT:-fp8}"
PORT="${LAMARK_VLLM_PORT:-8000}"
GPU_MEMORY_UTILIZATION="${LAMARK_GPU_UTIL:-0.85}"
MAX_LORAS="${LAMARK_MAX_LORAS:-4}"
MAX_LORA_RANK="${LAMARK_MAX_LORA_RANK:-64}"

# Phase 2 LoRA-serving flags. Off by default — only enable when
# adapters exist AND the installed vLLM version supports them.
# (vLLM 0.7.x doesn't have --enable-mixed-moe-lora-format yet; later
# versions per Kreuzhofer doc do.)
ENABLE_LORA="${LAMARK_ENABLE_LORA:-0}"

case "${1:-}" in
    --stop)
        docker stop "$CONTAINER_NAME" 2>/dev/null || echo "  (not running)"
        docker rm "$CONTAINER_NAME" 2>/dev/null || true
        exit 0
        ;;
    --logs)
        docker logs -f "$CONTAINER_NAME"
        exit 0
        ;;
    --status)
        if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
            echo "  RUNNING ($(docker ps --filter "name=$CONTAINER_NAME" --format '{{.Status}}'))"
            curl -fsS "http://127.0.0.1:$PORT/v1/models" 2>/dev/null \
                | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  models:", [m["id"] for m in d.get("data", [])])' \
                || echo "  (port $PORT not yet responding)"
        else
            echo "  NOT RUNNING"
        fi
        exit 0
        ;;
    --detach)
        DETACH_FLAG="--detach"
        ;;
    "")
        DETACH_FLAG=""
        ;;
    *)
        echo "usage: $0 [--detach | --stop | --logs | --status]" >&2
        exit 2
        ;;
esac

# Refuse to start if already up
if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "ERROR: container '$CONTAINER_NAME' already running. Use --stop first." >&2
    exit 4
fi

NGC_IMAGE="${NGC_IMAGE:-lamark/vllm:25.10}"

# vLLM serve args for MoE + LoRA hot-swap per Kreuzhofer recipe:
#   --enable-expert-parallel: required for MoE
#   --enable-lora --enable-mixed-moe-lora-format: prepare for Phase 2 adapters
#   --quantization fp8: FP8 block-128 serving
# We do NOT pre-register LoRA adapters here; they get loaded at Phase 2 time
# via /v1/load_lora_adapter when VLLM_ALLOW_RUNTIME_LORA_UPDATING is enabled.

CMD=(
    docker run
    $DETACH_FLAG
    --name "$CONTAINER_NAME"
    --restart=no
    --gpus all
    --network host
    --shm-size=16g
    --ulimit memlock=-1
    --ulimit stack=67108864
    -e HF_HOME=/workspace/.cache/huggingface
    -e VLLM_USE_V1=1
    -e VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
    -e TORCH_CUDA_ARCH_LIST=12.1
    # NGC PyTorch ships a flash_attn binary built against its base torch ABI;
    # pip install vllm pulls a different torch wheel and the ABI breaks
    # (undefined symbol on flash_attn_2_cuda import). Force TORCH_SDPA until
    # we either pin torch in the Dockerfile or rebuild flash_attn from source.
    -e VLLM_ATTENTION_BACKEND=TORCH_SDPA
    -e VLLM_USE_FLASH_ATTN=0
    -v "$LAMARK_MODEL_DIR:/workspace/models"
    -v "$HOME/.cache/huggingface:/workspace/.cache/huggingface"
    -v "$LAMARK_HOME/adapters:/workspace/adapters"
    -w /workspace
    "$NGC_IMAGE"
    bash -lc "
        set -e
        # NGC pytorch's pre-installed flash-attn has ABI mismatch with the
        # torch wheel that pip install vllm pulls in. Uninstall it so vLLM
        # falls back to torch SDPA (or its own native attention kernels).
        # Once we pin torch in the Dockerfile or rebuild flash-attn from
        # source against the matching torch, this can come out.
        pip uninstall -y flash-attn flash_attn 2>/dev/null || true

        # Activate the eager-loader patch (helps with mmap+CUDA double allocation)
        if [ -f /workspace/lamark-agent/scripts/eager_loader_patch.py ]; then
            export PYTHONSTARTUP=/workspace/lamark-agent/scripts/eager_loader_patch.py
        fi

        LORA_ARGS=''
        if [ '$ENABLE_LORA' = '1' ]; then
            LORA_ARGS=\"--enable-lora --max-loras $MAX_LORAS --max-lora-rank $MAX_LORA_RANK\"
        fi

        exec vllm serve '$PRIMARY_MODEL' \
            --quantization '$PRIMARY_QUANT' \
            --tensor-parallel-size 1 \
            --enable-expert-parallel \
            \$LORA_ARGS \
            --gpu-memory-utilization $GPU_MEMORY_UTILIZATION \
            --host 0.0.0.0 --port $PORT \
            --trust-remote-code
    "
)

echo "[lamark] starting vLLM ($PRIMARY_MODEL @ $PRIMARY_QUANT) ..."
echo "[lamark] container: $CONTAINER_NAME, port: $PORT"
if [ -n "$DETACH_FLAG" ]; then
    "${CMD[@]}"
    echo "[lamark] started detached; check with: $0 --status"
    echo "[lamark] tail logs:                    $0 --logs"
else
    exec "${CMD[@]}"
fi
