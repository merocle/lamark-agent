#!/usr/bin/env bash
# DGX Spark — pre-training cleanup.
#
# Stops all running containers, prunes Docker resources, and optionally
# clears the HuggingFace model cache. Run this before setting up new
# training containers to reclaim disk space and avoid stale container state.
#
# Usage:
#   ./00_cleanup.sh                    # stop containers + prune (keep pulled images)
#   ./00_cleanup.sh --all-images       # also remove every unused Docker image
#   ./00_cleanup.sh --hf-cache         # also clear the HF model cache (prompts)
#   ./00_cleanup.sh --all-images --hf-cache --yes   # non-interactive nuclear option
#
# Safe to run: does not touch model weights under LAMARK_MODEL_DIR.

set -euo pipefail

PRUNE_IMAGES=0
CLEAR_HF=0
YES=0
LAMARK_MODEL_DIR="${LAMARK_MODEL_DIR:-$HOME/.lamark/models}"

for arg in "$@"; do
    case "$arg" in
        --all-images) PRUNE_IMAGES=1 ;;
        --hf-cache)   CLEAR_HF=1 ;;
        --yes|-y)     YES=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[cleanup]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[cleanup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[cleanup]\033[0m %s\n" "$*"; }

confirm() {
    local prompt="$1"
    if [ "$YES" -eq 1 ]; then return 0; fi
    read -r -p "  $prompt [y/N] " ans
    [[ "${ans,,}" == "y" ]]
}

echo
log "=== DGX Spark pre-training cleanup ==="
echo

# 1. Stop all running containers
RUNNING=$(docker ps -q 2>/dev/null || true)
if [ -n "$RUNNING" ]; then
    COUNT=$(echo "$RUNNING" | wc -l | tr -d ' ')
    log "Stopping $COUNT running container(s)..."
    # shellcheck disable=SC2086
    docker stop $RUNNING
    ok "All running containers stopped."
else
    ok "No running containers."
fi

# 2. Remove stopped containers
log "Removing stopped containers..."
docker container prune -f
ok "Stopped containers removed."

# 3. Remove unused volumes
log "Removing unused volumes..."
docker volume prune -f
ok "Unused volumes removed."

# 4. Remove Docker build cache
log "Removing Docker build cache..."
docker builder prune -f
ok "Build cache cleared."

# 5. Prune images
if [ "$PRUNE_IMAGES" -eq 1 ]; then
    warn "--all-images: removing every unused Docker image (pulled NeMo/PyTorch images will be re-downloaded)."
    if confirm "Remove all unused Docker images?"; then
        docker image prune -a -f
        ok "All unused images removed."
    else
        warn "Image removal skipped."
    fi
else
    log "Removing dangling images (pass --all-images to remove pulled images too)..."
    docker image prune -f
    ok "Dangling images removed."
fi

# 6. Clear HuggingFace cache (optional — model weights live in LAMARK_MODEL_DIR, not here)
if [ "$CLEAR_HF" -eq 1 ]; then
    HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
    warn "--hf-cache: will delete $HF_CACHE (downloaded model weights)."
    if confirm "Delete HuggingFace cache at $HF_CACHE?"; then
        rm -rf "$HF_CACHE"
        ok "HF cache cleared."
    else
        warn "HF cache clear skipped."
    fi
fi

# 7. Summary
echo
log "Disk state after cleanup:"
df -h / | awk 'NR==1 || NR==2 { printf "  %s\n", $0 }'
echo
docker system df 2>/dev/null || true
echo
ok "Cleanup done. Ready to run 01_setup.sh."
