#!/bin/bash
# `lamark status` — one-screen health overview.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; }

echo "Lamark status"
echo "============="

# 1) Setup branch
if [ -f "$LAMARK_HOME/setup.json" ]; then
    BRANCH=$(python3 -c "import json,sys; print(json.load(open(r'$LAMARK_HOME/setup.json')).get('branch','?'))")
    TIER=$(python3 -c "import json,sys; print(json.load(open(r'$LAMARK_HOME/setup.json')).get('tier','?'))")
    ok "Setup: branch=$BRANCH, tier=$TIER"
else
    warn "Setup not complete. Run \`lamark setup\`."
fi

# 2) Hardware (live re-detect)
if [ -x "$VENV_PY" ]; then
    HW_JSON=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -m lamark.hardware 2>/dev/null || echo "{}")
    GPU=$(echo "$HW_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('hardware',{}).get('gpu_name') or 'CPU')" 2>/dev/null)
    MEM=$(echo "$HW_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(int(d.get('hardware',{}).get('effective_memory_gb') or 0))" 2>/dev/null)
    ok "Hardware: $GPU / ${MEM} GB"
fi

# 3) Model server
if docker ps --filter "name=lamark-vllm" --format "{{.Names}}" 2>/dev/null | grep -q lamark-vllm; then
    if curl -fsS -m 3 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
        MODELS=$(curl -fsS http://127.0.0.1:8000/v1/models | python3 -c "
import json, sys
ids = [m['id'] for m in json.load(sys.stdin)['data']]
print(', '.join(ids))
")
        ok "Model server: up — serving [$MODELS]"
    else
        warn "Model server container running but /v1/models not reachable (loading?)."
    fi
elif [ -f "$LAMARK_HOME/setup.json" ] && grep -q "existing-endpoint\|cloud-first" "$LAMARK_HOME/setup.json" 2>/dev/null; then
    # Probe configured endpoint
    LM_BASE_URL=$(grep -E "^export LM_BASE_URL" "$HERMES_HOME/env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
    if [ -n "$LM_BASE_URL" ] && curl -fsS -m 3 "${LM_BASE_URL}/models" >/dev/null 2>&1; then
        ok "Remote endpoint: $LM_BASE_URL — reachable"
    else
        warn "Remote endpoint configured but not reachable."
    fi
else
    bad "Model server: not running. \`lamark serve start\`"
fi

# 4) Adapters
if [ -d "$LAMARK_HOME/adapters" ]; then
    N_AD=$(find "$LAMARK_HOME/adapters" -maxdepth 2 -name adapter_config.json 2>/dev/null | wc -l | tr -d ' ')
    if [ "$N_AD" -gt 0 ]; then
        ok "LoRA adapters: $N_AD trained"
    else
        warn "No LoRA adapters yet. Will accumulate via nightly retrain."
    fi
else
    warn "Adapter directory missing."
fi

# 5) Background download (cloud-first branch)
if [ -f "$LAMARK_HOME/download.pid" ]; then
    DPID=$(cat "$LAMARK_HOME/download.pid")
    if kill -0 "$DPID" 2>/dev/null; then
        ok "Background model download in progress (pid $DPID). \`lamark logs download\`"
    else
        ok "Background download completed."
        rm -f "$LAMARK_HOME/download.pid"
    fi
fi

# 6) Nightly retrain timer (systemd)
USER_NAME="${SUDO_USER:-$USER}"
if systemctl list-timers --all 2>/dev/null | grep -q "lamark-nightly@${USER_NAME}"; then
    NEXT=$(systemctl list-timers "lamark-nightly@${USER_NAME}.timer" --no-pager --no-legend 2>/dev/null | awk '{print $1, $2}')
    ok "Nightly retrain timer: next at $NEXT"
else
    warn "Nightly retrain timer not installed."
fi

# 7) Last training log
LAST_TRAIN_LOG=$(ls -t "$LAMARK_HOME/logs"/nightly-train-*.log 2>/dev/null | head -1 || true)
if [ -n "${LAST_TRAIN_LOG:-}" ]; then
    LAST=$(grep -E "PASS|FAIL" "$LAST_TRAIN_LOG" 2>/dev/null | tail -1 || echo "still running")
    ok "Last train ($(basename "$LAST_TRAIN_LOG")): $LAST"
fi

# 8) Memory + USER.md
if [ -f "$HERMES_HOME/memories/USER.md" ]; then
    LINES=$(wc -l < "$HERMES_HOME/memories/USER.md" | tr -d ' ')
    ok "USER.md: $LINES lines of remembered facts"
fi
if [ -f "$HERMES_HOME/memories/MEMORY.md" ]; then
    LINES=$(wc -l < "$HERMES_HOME/memories/MEMORY.md" | tr -d ' ')
    ok "MEMORY.md: $LINES lines"
fi

echo ""
