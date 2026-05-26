#!/usr/bin/env bash
# Lamark — alpha installer.
#
# One-command install for new users:
#     curl -fsSL https://raw.githubusercontent.com/Merocle/lamark-agent/main/install.sh | bash
#
# After this finishes:
#     lamark setup        # interactive wizard (Local / Existing endpoint / Cloud-first)
#     lamark serve start  # if you chose Local
#     lamark chat         # talk to your agent
#
# This script only sets up the *infrastructure* (repo, venv, deps, dispatcher
# symlink, hermes-home skeleton). Model download and provider selection are
# deferred to `lamark setup` so the user gets to choose between local serving,
# an existing OpenAI-compatible endpoint they already run, or a cloud provider.
#
# Environment overrides (mostly for development / testing):
#   LAMARK_HOME          where runtime state lives (default ~/.lamark)
#   LAMARK_REPO          where the repo gets cloned (default ~/lamark-agent)
#   LAMARK_REPO_URL      git URL to clone (default github.com/Merocle/lamark-agent)
#   LAMARK_REPO_REF      branch/tag/sha to checkout (default main)
#   LAMARK_INSTALL_VERBOSE=1   stream subprocess output
#   LAMARK_INSTALL_DRY_RUN=1   print steps without executing
#   LAMARK_SKIP_DEP_CHECK=1    don't fail if docker/nvidia missing (CI usage)
set -euo pipefail

# ============================================================
# Settings + colours
# ============================================================
LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
LAMARK_REPO_URL="${LAMARK_REPO_URL:-https://github.com/Merocle/lamark-agent.git}"
LAMARK_REPO_REF="${LAMARK_REPO_REF:-main}"
LAMARK_INSTALL_VERBOSE="${LAMARK_INSTALL_VERBOSE:-0}"
LAMARK_INSTALL_DRY_RUN="${LAMARK_INSTALL_DRY_RUN:-0}"
LAMARK_SKIP_DEP_CHECK="${LAMARK_SKIP_DEP_CHECK:-0}"

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
    C_BLUE=$'\033[34m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_ERR=""; C_BLUE=""; C_DIM=""; C_RST=""
fi
ok()   { printf "%s✓%s %s\n" "$C_OK" "$C_RST" "$*"; }
warn() { printf "%s⚠%s %s\n" "$C_WARN" "$C_RST" "$*"; }
err()  { printf "%s✗%s %s\n" "$C_ERR" "$C_RST" "$*" >&2; }
step() { printf "%s▶%s %s\n" "$C_BLUE" "$C_RST" "$*"; }
dim()  { printf "%s%s%s\n" "$C_DIM" "$*" "$C_RST"; }

die() { err "$*"; exit 1; }
run() {
    # Execute a command, respecting dry-run and verbose.
    if [ "$LAMARK_INSTALL_DRY_RUN" = "1" ]; then
        dim "(dry-run) $*"
        return 0
    fi
    if [ "$LAMARK_INSTALL_VERBOSE" = "1" ]; then
        eval "$@"
    else
        eval "$@" >/tmp/lamark-install.last.log 2>&1 || {
            err "command failed: $*"
            err "tail of output:"
            tail -20 /tmp/lamark-install.last.log >&2 || true
            exit 1
        }
    fi
}

# ============================================================
# Banner
# ============================================================
cat <<EOF

  ${C_BLUE}Lamark${C_RST} — locally-hosted personal AI agent
  ${C_DIM}alpha installer · sets up infra, then \`lamark setup\` wizard${C_RST}

EOF

# ============================================================
# Preflight — OS / arch / dependencies
# ============================================================
step "Preflight check"

UNAME_S=$(uname -s)
UNAME_M=$(uname -m)

case "$UNAME_S" in
    Linux)  ok "OS: Linux" ;;
    Darwin) warn "macOS detected. Lamark is Linux-first (DGX Spark and CUDA GPUs)." ;;
    *)      die "Unsupported OS: $UNAME_S" ;;
esac

case "$UNAME_M" in
    aarch64|arm64) ok "Arch: aarch64 (Spark / ARM)" ;;
    x86_64|amd64)  ok "Arch: x86_64" ;;
    *)             warn "Unusual arch: $UNAME_M — proceeding but untested." ;;
esac

# GPU presence
HAS_NVIDIA=0
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$GPU_NAME" ]; then
        ok "GPU: $GPU_NAME"
        HAS_NVIDIA=1
    fi
fi
if [ "$HAS_NVIDIA" = "0" ] && [ "$LAMARK_SKIP_DEP_CHECK" != "1" ]; then
    warn "No NVIDIA GPU detected. Lamark currently requires CUDA hardware for local serving."
    warn "You can still install and use the 'existing endpoint' or 'cloud-first' setup branches."
fi

# Required dependencies
need_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        ok "$1: $(command -v "$1")"
    else
        if [ "$LAMARK_SKIP_DEP_CHECK" = "1" ]; then
            warn "$1 not found (skip-dep-check is on)"
            return 0
        fi
        err "$1 not found."
        case "$1" in
            python3) err "  Install: sudo apt install -y python3 python3-venv python3-pip" ;;
            git)     err "  Install: sudo apt install -y git" ;;
            curl)    err "  Install: sudo apt install -y curl" ;;
            docker)  err "  Install: see https://docs.docker.com/engine/install/" ;;
        esac
        exit 1
    fi
}
need_cmd python3
need_cmd git
need_cmd curl

# Docker is required for local serving but optional for existing-endpoint/cloud-first.
if command -v docker >/dev/null 2>&1; then
    ok "docker: $(docker --version 2>/dev/null | head -1)"
else
    warn "Docker not found — needed only for local model serving."
    warn "  Install later via https://docs.docker.com/engine/install/ if you want \`lamark serve\`."
fi

# nvidia-container-toolkit (nvidia-container-cli) — needed if both Docker and GPU exist
if [ "$HAS_NVIDIA" = "1" ] && command -v docker >/dev/null 2>&1; then
    if command -v nvidia-container-cli >/dev/null 2>&1; then
        ok "nvidia-container-toolkit: $(nvidia-container-cli --version 2>&1 | head -1)"
    else
        warn "nvidia-container-toolkit not found — Docker won't see the GPU."
        warn "  Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
    fi
fi

echo ""

# ============================================================
# Clone / update repo
# ============================================================
step "Fetching Lamark repo into $LAMARK_REPO"

if [ -d "$LAMARK_REPO/.git" ]; then
    # Already cloned — fast-forward to the configured ref.
    dim "Repo exists; updating to $LAMARK_REPO_REF"
    run "git -C '$LAMARK_REPO' fetch --depth 1 origin '$LAMARK_REPO_REF'"
    run "git -C '$LAMARK_REPO' checkout '$LAMARK_REPO_REF'"
    run "git -C '$LAMARK_REPO' reset --hard 'origin/$LAMARK_REPO_REF' || true"
    ok "Repo updated"
elif [ -d "$LAMARK_REPO" ] && [ -f "$LAMARK_REPO/scripts/lamark" ]; then
    # Local copy exists but isn't a git checkout (e.g. rsync from a dev machine).
    # Trust the user's local copy — don't overwrite.
    ok "Local repo at $LAMARK_REPO (no .git, leaving as-is)"
else
    run "git clone --depth 1 --branch '$LAMARK_REPO_REF' '$LAMARK_REPO_URL' '$LAMARK_REPO'"
    ok "Repo cloned"
fi

echo ""

# ============================================================
# Create venv + install Python deps
# ============================================================
step "Setting up Python venv at $LAMARK_HOME/venv"

mkdir -p "$LAMARK_HOME/bin" "$LAMARK_HOME/logs" "$LAMARK_HOME/adapters" \
         "$LAMARK_HOME/models" "$LAMARK_HOME/hermes-home/memories"

if [ ! -d "$LAMARK_HOME/venv" ]; then
    # --system-site-packages so we can borrow the host's torch on Spark
    # (PyTorch wheels for sm_121 + cu130 aren't on PyPI, the OS install is).
    run "python3 -m venv '$LAMARK_HOME/venv' --system-site-packages"
    ok "venv created"
else
    ok "venv exists"
fi

run "'$LAMARK_HOME/venv/bin/pip' install -q --upgrade pip"

# Core Python deps. Hermes core + memory + minimum to drive the dispatcher.
# We don't install the messaging/exa/firecrawl extras — those get lazy-installed
# by `hermes setup` if the user enables them.
DEPS=(
    "pyyaml>=6.0"
    "huggingface_hub>=0.20"
    "openai==2.24.0"
    "python-dotenv==1.2.2"
    "fire==0.7.1"
    "httpx[socks]==0.28.1"
    "rich==14.3.3"
    "tenacity==9.1.4"
    "ruamel.yaml==0.18.17"
    "requests>=2.32"
    "jinja2==3.1.6"
    "pydantic==2.13.4"
    "prompt_toolkit==3.0.52"
    "croniter==6.0.0"
    "PyJWT[crypto]==2.12.1"
    "psutil==7.2.2"
    "sqlalchemy>=2"
)
step "Installing Python deps (Hermes core + memory)"
run "'$LAMARK_HOME/venv/bin/pip' install -q ${DEPS[*]}"
ok "Python deps installed"

echo ""

# ============================================================
# Symlink dispatcher into ~/.lamark/bin
# ============================================================
step "Wiring up the \`lamark\` command"

chmod +x "$LAMARK_REPO/scripts/lamark"
find "$LAMARK_REPO/scripts/cmd" -name "*.sh" -exec chmod +x {} \;
chmod +x "$LAMARK_REPO/scripts/lamark-nightly-train.sh" 2>/dev/null || true

ln -sf "$LAMARK_REPO/scripts/lamark" "$LAMARK_HOME/bin/lamark"
ok "Dispatcher at $LAMARK_HOME/bin/lamark"

# Make sure $LAMARK_HOME/bin is in the user's PATH. We append to the shell's
# rc file *only if it's not already there* — idempotent.
SHELL_RC=""
case "$(basename "${SHELL:-/bin/bash}")" in
    bash) SHELL_RC="$HOME/.bashrc" ;;
    zsh)  SHELL_RC="$HOME/.zshrc" ;;
    *)    SHELL_RC="" ;;
esac
PATH_LINE="export PATH=\"$LAMARK_HOME/bin:\$PATH\""
if [ -n "$SHELL_RC" ]; then
    if [ -f "$SHELL_RC" ] && grep -qF "$LAMARK_HOME/bin" "$SHELL_RC" 2>/dev/null; then
        ok "PATH already configured in $(basename "$SHELL_RC")"
    elif [ -w "$HOME" ]; then
        if [ "$LAMARK_INSTALL_DRY_RUN" = "1" ]; then
            dim "(dry-run) would append PATH export to $(basename "$SHELL_RC")"
        else
            printf '\n# Added by Lamark installer\n%s\n' "$PATH_LINE" >> "$SHELL_RC"
            ok "Appended PATH export to $(basename "$SHELL_RC")"
            warn "Reload your shell (or run \`source $SHELL_RC\`) to pick up \`lamark\`."
        fi
    fi
fi

echo ""

# ============================================================
# Hardware tier preview (informational)
# ============================================================
step "Hardware tier preview"
HW_JSON=$(PYTHONPATH="$LAMARK_REPO/src" "$LAMARK_HOME/venv/bin/python" -m lamark.hardware 2>/dev/null || echo '{}')
TIER=$(echo "$HW_JSON" | "$LAMARK_HOME/venv/bin/python" -c "import json,sys; d=json.load(sys.stdin); print(d.get('tier','NONE'))" 2>/dev/null || echo "NONE")
REC=$(echo "$HW_JSON" | "$LAMARK_HOME/venv/bin/python" -c "import json,sys; d=json.load(sys.stdin); print(d.get('recommended_model','') or '(none)')" 2>/dev/null || echo "(unknown)")
ok "Tier: $TIER · recommended model: $REC"

echo ""

# ============================================================
# Done
# ============================================================
cat <<EOF

${C_OK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RST}
${C_OK}  Lamark infrastructure installed.${C_RST}
${C_OK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RST}

Next:
  ${C_BLUE}\$ lamark setup${C_RST}        Interactive wizard — picks Local / Existing endpoint / Cloud-first
  ${C_BLUE}\$ lamark status${C_RST}       Show overall health any time

Locations:
  Repo:        $LAMARK_REPO
  Runtime:     $LAMARK_HOME
  Dispatcher:  $LAMARK_HOME/bin/lamark
  venv:        $LAMARK_HOME/venv

If \`lamark\` isn't on PATH yet, run:
  ${C_BLUE}export PATH="$LAMARK_HOME/bin:\$PATH"${C_RST}

EOF
