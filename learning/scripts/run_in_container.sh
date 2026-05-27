#!/usr/bin/env bash
# Lamark — run a command inside the NVIDIA NGC PyTorch container on DGX Spark.
#
# Why a container: torch+CUDA for sm_121 (GB10 Blackwell) on aarch64 is not
# available as a stable pip wheel today (May 2026). The canonical Kreuzhofer
# path is `nvcr.io/nvidia/pytorch:25.10-py3` which ships CUDA-enabled torch,
# vLLM, transformers, and tested Spark kernels.
#
# This wrapper mounts the user's model dir, adapter dir, HF cache, and the
# Lamark repo into the container under /workspace, then runs the given command.
#
# Usage:
#   ./scripts/run_in_container.sh python scripts/smoke_test.py --quick
#   ./scripts/run_in_container.sh vllm serve Qwen/Qwen3.6-35B-A3B \
#       --quantization fp8 --enable-expert-parallel
#   ./scripts/run_in_container.sh bash       # interactive shell

set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$LAMARK_HOME/models}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
NGC_IMAGE="${NGC_IMAGE:-nvcr.io/nvidia/pytorch:25.10-py3}"
CONTAINER_NAME_PREFIX="${CONTAINER_NAME_PREFIX:-lamark}"

# Detached / interactive
DETACH=""
NAME_ARG=()
INTERACTIVE_FLAGS="-it"
if [[ "${1:-}" == "--detach" ]]; then
    DETACH="--detach"
    INTERACTIVE_FLAGS=""
    shift
    NAME_ARG=(--name "${CONTAINER_NAME_PREFIX}-$(date +%s)")
elif [[ "${1:-}" == "--name" ]]; then
    NAME_ARG=(--name "$2")
    shift 2
fi

if [ ! -d "$LAMARK_MODEL_DIR" ]; then
    echo "ERROR: $LAMARK_MODEL_DIR does not exist. Run setup_spark.sh first." >&2
    exit 2
fi

if ! docker image inspect "$NGC_IMAGE" >/dev/null 2>&1; then
    echo "ERROR: $NGC_IMAGE not present locally." >&2
    echo "       Run: docker pull $NGC_IMAGE" >&2
    exit 3
fi

# Build common docker flags. Notes:
#   --gpus all: expose GB10 to the container (requires nvidia-container-toolkit)
#   --network host: simplest for single-user (vLLM server on 127.0.0.1:8000)
#   --shm-size=16g: torch DataLoader / NCCL need generous /dev/shm
#   --ulimit memlock=-1: required for some CUDA pinned-memory paths
#   --ulimit stack=67108864: NCCL recommends 64M stack
DOCKER_ARGS=(
    "$DETACH"
    "$INTERACTIVE_FLAGS"
    "${NAME_ARG[@]}"
    --rm
    --gpus all
    --network host
    --shm-size=16g
    --ulimit memlock=-1
    --ulimit stack=67108864
    -e HF_HOME=/workspace/.cache/huggingface
    -e HF_HUB_ENABLE_HF_TRANSFER=1
    -e TORCH_CUDA_ARCH_LIST=12.1
    -e VLLM_USE_V1=1
    -e VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
    -v "$LAMARK_MODEL_DIR:/workspace/models"
    -v "$HF_CACHE:/workspace/.cache/huggingface"
    -v "$LAMARK_HOME/adapters:/workspace/adapters"
    -v "$LAMARK_REPO:/workspace/lamark-agent"
    -w /workspace/lamark-agent
)

# Filter out empty strings (DETACH/INTERACTIVE_FLAGS may be empty)
FILTERED_ARGS=()
for arg in "${DOCKER_ARGS[@]}"; do
    [ -n "$arg" ] && FILTERED_ARGS+=("$arg")
done

exec docker run "${FILTERED_ARGS[@]}" "$NGC_IMAGE" "$@"
