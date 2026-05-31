#!/usr/bin/env bash
# Lamark — DGX Spark provisioning script.
#
# Installs vLLM + llama.cpp + Unsloth + the Kreuzhofer eager-loader patch,
# then downloads Qwen3.6-35B-A3B (primary) and Qwen3.6-27B (Phase 2 fine-tune target).
#
# Idempotent: safe to re-run. Each step skipped if already done.
#
# Requirements assumed pre-existing on Spark (DGX OS Ubuntu 24.04 ships these):
#   - CUDA 13.x toolkit
#   - python3.11 or python3.12
#   - git
#   - 4 TB internal SSD (or larger external NVMe mounted at $LAMARK_MODEL_DIR)
#
# Usage on Spark:
#   ./setup_spark.sh                                    # full setup
#   ./setup_spark.sh --skip-models                      # tooling only
#   ./setup_spark.sh --primary-only                     # skip Qwen3.6-27B dense
#   LAMARK_MODEL_DIR=/mnt/nvme/models ./setup_spark.sh  # custom model path

set -euo pipefail

# ---- configuration ---------------------------------------------------------
LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_VENV="${LAMARK_VENV:-$LAMARK_HOME/venv}"
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$LAMARK_HOME/models}"
LAMARK_BIN_DIR="${LAMARK_BIN_DIR:-$LAMARK_HOME/bin}"

# Resolve the repo root from this script's location so the launcher symlink
# and config-template references work regardless of where the script is run.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PRIMARY_MODEL="Qwen/Qwen3.6-35B-A3B"
DENSE_MODEL="Qwen/Qwen3.6-27B"

# Known-good pin set as of 2026-05; bump only after re-running smoke_test.py
VLLM_VERSION="${VLLM_VERSION:-0.7.3}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu130}"

# Parse flags
SKIP_MODELS=0
PRIMARY_ONLY=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --skip-models)   SKIP_MODELS=1 ;;
        --primary-only)  PRIMARY_ONLY=1 ;;
        --force)         FORCE=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[lamark]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[lamark]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[lamark]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[lamark]\033[0m %s\n" "$*" >&2; }

# ---- preflight -------------------------------------------------------------
log "Preflight checks..."

if ! command -v python3.11 >/dev/null && ! command -v python3.12 >/dev/null; then
    err "Need python3.11 or python3.12. Install with: sudo apt install python3.11-venv python3.11-dev"
    exit 1
fi
PYTHON=$(command -v python3.11 || command -v python3.12)
log "Using $PYTHON ($($PYTHON --version))"

if ! command -v nvidia-smi >/dev/null; then
    err "nvidia-smi not found. Are you on a Spark? Is CUDA installed?"
    exit 1
fi
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
log "GPU: $GPU_NAME"
if [[ "$GPU_NAME" != *"GB10"* && "$GPU_NAME" != *"Grace"* && "$GPU_NAME" != *"Blackwell"* ]]; then
    warn "GPU name doesn't match DGX Spark (GB10/Grace/Blackwell). Continuing anyway."
fi

mkdir -p "$LAMARK_HOME" "$LAMARK_MODEL_DIR" "$LAMARK_BIN_DIR"

# ---- venv ------------------------------------------------------------------
if [ ! -d "$LAMARK_VENV" ] || [ "$FORCE" -eq 1 ]; then
    log "Creating venv at $LAMARK_VENV"
    [ "$FORCE" -eq 1 ] && rm -rf "$LAMARK_VENV"
    "$PYTHON" -m venv "$LAMARK_VENV"
fi

# shellcheck disable=SC1091
source "$LAMARK_VENV/bin/activate"
pip install --upgrade pip wheel setuptools >/dev/null

# ---- environment hints for sm_121 ------------------------------------------
log "Configuring environment for GB10 sm_121..."
ENV_FILE="$LAMARK_HOME/env"
cat > "$ENV_FILE" <<EOF
# Lamark Spark environment (sourced by lamark CLI and scripts)
export TORCH_CUDA_ARCH_LIST=12.1
export VLLM_USE_V1=1
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
export LAMARK_HOME=$LAMARK_HOME
export LAMARK_VENV=$LAMARK_VENV
export LAMARK_MODEL_DIR=$LAMARK_MODEL_DIR
export HF_HOME=$LAMARK_MODEL_DIR/hf
export HF_HUB_ENABLE_HF_TRANSFER=1
EOF
# shellcheck disable=SC1090
source "$ENV_FILE"

# ---- LEGACY native ML install ---------------------------------------------
# NOTE: the canonical ML paths are CONTAINERS, not this host env:
#   - serving  → `lamark serve` runs vllm/vllm-openai:v0.21.0
#   - training → the nightly trainer runs lamark/vllm:25.10 (docker/Dockerfile.vllm)
# The host venv is intentionally thin/CPU-only (aarch64 torch wheels are
# unreliable). The native installs below are a legacy fallback and are NOT
# version-aligned with the containers (e.g. the "<5" transformers cap predates
# the move to transformers 5.x). Prefer the containers; don't rely on this.
#
# ---- pytorch (must match CUDA 13.x) ---------------------------------------
if ! python -c "import torch" 2>/dev/null; then
    log "Installing torch from $TORCH_INDEX_URL"
    pip install --index-url "$TORCH_INDEX_URL" "torch>=2.4,<3"
else
    ok "torch already installed: $(python -c 'import torch; print(torch.__version__)')"
fi

# ---- vLLM ------------------------------------------------------------------
if ! python -c "import vllm" 2>/dev/null; then
    log "Installing vLLM==$VLLM_VERSION"
    pip install "vllm==$VLLM_VERSION"
else
    ok "vLLM already installed: $(python -c 'import vllm; print(vllm.__version__)')"
fi

# ---- supporting libs (transformers, peft, trl, accelerate, hf_transfer) ----
log "Installing supporting libraries..."
pip install \
    "transformers>=4.45,<5" \
    "accelerate>=0.34" \
    "peft>=0.13" \
    "trl>=0.11" \
    "hf_transfer>=0.1.8" \
    "sentencepiece>=0.2.0" \
    "protobuf>=4.25"

# ---- Unsloth + fused MoE kernel for Qwen3.6 -------------------------------
if ! python -c "import unsloth" 2>/dev/null; then
    log "Installing Unsloth (with fused Qwen3-MoE kernel)..."
    pip install "unsloth[cu130-torch24]>=2024.10" || pip install "unsloth>=2024.10"
else
    ok "Unsloth already installed: $(python -c 'import unsloth; print(unsloth.__version__)')"
fi

# ---- llama.cpp (built for sm_121 + Q4 GGUF serving of dense model) --------
LLAMACPP_DIR="$LAMARK_HOME/llama.cpp"
if [ ! -d "$LLAMACPP_DIR" ] || [ "$FORCE" -eq 1 ]; then
    log "Building llama.cpp for GB10 sm_121..."
    [ "$FORCE" -eq 1 ] && rm -rf "$LLAMACPP_DIR"
    git clone --depth 1 https://github.com/ggerganov/llama.cpp "$LLAMACPP_DIR"
    (
        cd "$LLAMACPP_DIR"
        cmake -B build \
            -DGGML_CUDA=ON \
            -DCMAKE_CUDA_ARCHITECTURES=121 \
            -DBUILD_SHARED_LIBS=OFF \
            -DCMAKE_BUILD_TYPE=Release
        cmake --build build -j --target llama-server llama-cli
    )
    ln -sf "$LLAMACPP_DIR/build/bin/llama-server" "$LAMARK_BIN_DIR/llama-server"
    ln -sf "$LLAMACPP_DIR/build/bin/llama-cli"    "$LAMARK_BIN_DIR/llama-cli"
    ok "llama.cpp built and symlinked into $LAMARK_BIN_DIR"
else
    ok "llama.cpp already built at $LLAMACPP_DIR"
fi

# ---- Kreuzhofer eager-loader patch ----------------------------------------
# Reference: forums.developer.nvidia.com/t/.../363268
# Patches safe_open() to call posix_fadvise(POSIX_FADV_DONTNEED) after each shard,
# avoiding the mmap+CUDA double allocation OOM at 66% load on UMA.
PATCH_FILE="$LAMARK_HOME/eager_loader_patch.py"
if [ ! -f "$PATCH_FILE" ] || [ "$FORCE" -eq 1 ]; then
    log "Installing Kreuzhofer eager-loader patch..."
    cat > "$PATCH_FILE" <<'PATCH'
"""
Eager direct-to-CUDA safetensors loader. Hooks safetensors.safe_open to drop
page cache after each shard, avoiding mmap + CUDA double allocation OOM on
DGX Spark unified memory.

v2: replaced the @contextmanager generator wrapper with a proxy class that
exposes both context-manager AND direct attribute access. transformers >= 5.9
calls `safe_open(...).keys()` outside any `with` block and the old wrapper
returned a _GeneratorContextManager that had no .keys.

Reference: Daniel Kreuzhofer, NVIDIA Developer Forum
  https://forums.developer.nvidia.com/t/bf16-lora-fine-tuning-of-qwen3-5-35b-a3b-on-dgx-spark-no-quantization-required/363268
"""
from __future__ import annotations

import ctypes
import os

try:
    import safetensors  # type: ignore
except ImportError:
    safetensors = None  # type: ignore

_POSIX_FADV_DONTNEED = 4


def _fadvise_dontneed(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.posix_fadvise(fd, ctypes.c_int64(0), ctypes.c_int64(0), ctypes.c_int(_POSIX_FADV_DONTNEED))
        finally:
            os.close(fd)
    except OSError:
        pass


class _EagerSafeOpen:
    """Proxy around the real SafeOpen supporting both context-manager use
    and direct attribute access (.keys, .get_tensor, etc.)."""

    def __init__(self, filename, framework="pt", device="cpu"):
        self._filename = filename
        self._inner = safetensors._orig_safe_open(filename, framework=framework, device=device)

    def __enter__(self):
        if hasattr(self._inner, "__enter__"):
            return self._inner.__enter__()
        return self._inner

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if hasattr(self._inner, "__exit__"):
                return self._inner.__exit__(exc_type, exc_val, exc_tb)
        finally:
            _fadvise_dontneed(str(self._filename))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def install() -> None:
    if safetensors is None:
        return
    if getattr(safetensors, "_lamark_patched", False):
        return
    safetensors._orig_safe_open = safetensors.safe_open  # type: ignore[attr-defined]
    safetensors.safe_open = _EagerSafeOpen  # type: ignore[attr-defined]
    safetensors._lamark_patched = True  # type: ignore[attr-defined]


install()
PATCH
    ok "Eager-loader patch written to $PATCH_FILE"
else
    ok "Eager-loader patch already in place"
fi

# ---- hermes-home config + Lamark launcher --------------------------------
HERMES_HOME_DIR="$LAMARK_HOME/hermes-home"
mkdir -p "$HERMES_HOME_DIR"

CFG_FILE="$HERMES_HOME_DIR/config.yaml"
if [ ! -f "$CFG_FILE" ] || [ "$FORCE" -eq 1 ]; then
    cp "$REPO_ROOT/scripts/hermes-home-template/config.yaml" "$CFG_FILE"
    ok "Hermes-home config installed at $CFG_FILE"
else
    ok "Hermes-home config already in place"
fi

ENV_FILE="$HERMES_HOME_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
LM_API_KEY=not-needed
LM_BASE_URL=http://127.0.0.1:8000/v1
EOF
    ok "Hermes-home .env initialised"
fi

LAUNCHER_BIN="$LAMARK_BIN_DIR/lamark"
mkdir -p "$LAMARK_BIN_DIR"
# Make every cmd/*.sh executable and symlink the dispatcher into $LAMARK_BIN_DIR.
chmod +x "$REPO_ROOT/scripts/lamark" 2>/dev/null || true
find "$REPO_ROOT/scripts/cmd" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true

if [ ! -L "$LAUNCHER_BIN" ] || [ "$FORCE" -eq 1 ]; then
    ln -sf "$REPO_ROOT/scripts/lamark" "$LAUNCHER_BIN"
    ok "Lamark dispatcher symlinked to $LAUNCHER_BIN"
else
    ok "Lamark dispatcher already in place"
fi

# ---- nightly retrain timer (systemd) --------------------------------------
chmod +x "$REPO_ROOT/scripts/lamark-nightly-train.sh" 2>/dev/null || true

if [ "${SKIP_TIMER:-0}" -eq 1 ]; then
    warn "Skipping nightly retrain timer install (SKIP_TIMER=1)"
elif command -v systemctl >/dev/null 2>&1; then
    # USER-scope timer (no sudo) via the same path as `lamark train
    # --schedule` — writes ~/.config/systemd/user units and enables lingering.
    SPEC="${LAMARK_SCHEDULE:-*-*-* 03:00:00}"
    if LAMARK_REPO="$REPO_ROOT" "$REPO_ROOT/scripts/cmd/train.sh" --schedule "$SPEC" >/dev/null 2>&1; then
        ok "Nightly retrain timer installed (user-scope, no sudo): $SPEC"
    else
        warn "Could not enable the user-scope timer. Set it with:"
        warn "  lamark train --schedule \"$SPEC\""
    fi
else
    warn "systemctl not found; nightly timer not installed."
fi

# ---- hardware detection + tier-aware model selection ---------------------
log "Detecting hardware tier..."
HW_JSON=$(PYTHONPATH="$REPO_ROOT/src" "$LAMARK_VENV/bin/python" -m lamark.hardware 2>/dev/null || echo "{}")
TIER=$(echo "$HW_JSON" | "$LAMARK_VENV/bin/python" -c "import json,sys; d=json.load(sys.stdin); print(d.get('tier','NONE'))")
RECOMMENDED_MODEL=$(echo "$HW_JSON" | "$LAMARK_VENV/bin/python" -c "import json,sys; d=json.load(sys.stdin); print(d.get('recommended_model') or '')")

if [ "$TIER" = "NONE" ] || [ -z "$RECOMMENDED_MODEL" ]; then
    warn "Hardware tier could not be determined. Falling back to legacy 35B-A3B."
    RECOMMENDED_MODEL="qwen-3.6-35b-a3b-moe"
else
    ok "Hardware tier: $TIER → default model: $RECOMMENDED_MODEL"
fi

# Allow the user (or downstream tooling) to override via env var.
LAMARK_MODEL="${LAMARK_MODEL:-$RECOMMENDED_MODEL}"

# Look up the HF id from the registry.
HF_ID=$(PYTHONPATH="$REPO_ROOT/src" "$LAMARK_VENV/bin/python" -c "
from lamark.registry import get_model
print(get_model('$LAMARK_MODEL').hf_id)
" 2>/dev/null)
if [ -z "$HF_ID" ]; then
    fail "Model '$LAMARK_MODEL' not in scripts/model-registry.yaml. Aborting."
fi
ok "Will download $HF_ID for model '$LAMARK_MODEL'"

# ---- model downloads ------------------------------------------------------
if [ "$SKIP_MODELS" -eq 1 ]; then
    warn "Skipping model downloads (--skip-models)"
else
    log "Downloading models to $LAMARK_MODEL_DIR/hf ..."

    download_model() {
        local repo="$1"
        local local_dir="$LAMARK_MODEL_DIR/hf/$(echo "$repo" | tr '/' '_')"
        if [ -d "$local_dir" ] && [ "$FORCE" -eq 0 ]; then
            ok "Model $repo already present at $local_dir"
            return
        fi
        log "Downloading $repo (this may take 30-60 min on first run)..."
        PYTHONPATH="$REPO_ROOT/src" "$LAMARK_VENV/bin/python" -c "
from lamark.download import fetch_model
fetch_model('$repo', r'$local_dir', max_workers=8)
print('downloaded:', '$repo')
"
        ok "Model $repo ready at $local_dir"
    }

    download_model "$HF_ID"
fi

# ---- final summary --------------------------------------------------------
echo
echo "=================================================================="
ok "Lamark provisioning complete."
echo "=================================================================="
echo
echo "Detected hardware tier: $TIER"
echo "Default model:          $LAMARK_MODEL ($HF_ID)"
echo
echo "Next steps:"
echo "  1) source $ENV_FILE"
echo "  2) $LAMARK_BIN_DIR/lamark chat"
echo "     (or: $LAMARK_BIN_DIR/lamark switch-base <model-name> first)"
echo
echo "Locations:"
echo "  venv:    $LAMARK_VENV"
echo "  models:  $LAMARK_MODEL_DIR"
echo "  bin:     $LAMARK_BIN_DIR"
echo "  env:     $ENV_FILE"
echo
echo "Available models (lamark switch-base <name>):"
PYTHONPATH="$REPO_ROOT/src" "$LAMARK_VENV/bin/python" -c "
from lamark.registry import load_registry
for name, entry in load_registry().items():
    if hasattr(entry, 'hf_id'):
        print(f'  {name:<28} tier={entry.tier} arch={entry.arch:<5} hf={entry.hf_id}')
"
echo
