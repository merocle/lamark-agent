#!/bin/bash
# `lamark chat` — open interactive chat against the configured model.
#
# Forwards all args to Hermes's main(). Sets env so the model+provider
# resolve to whatever the user picked during `lamark setup`. Pre-flight
# checks that vLLM (or the user-supplied endpoint) is reachable.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"

# Hermes runtime state lives under hermes-home inside Lamark home.
export HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"

# Source the env file written by `lamark setup` (LM_BASE_URL, LM_API_KEY,
# HERMES_INFERENCE_PROVIDER, HERMES_INFERENCE_MODEL) — but allow caller
# overrides via real env vars.
ENV_FILE="$HERMES_HOME/env"
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

# Sensible defaults if no setup has been run yet (local vLLM on :8000).
export LM_API_KEY="${LM_API_KEY:-not-needed}"
export LM_BASE_URL="${LM_BASE_URL:-http://127.0.0.1:8000/v1}"
export HERMES_INFERENCE_PROVIDER="${HERMES_INFERENCE_PROVIDER:-lm-studio}"
export HERMES_INFERENCE_MODEL="${HERMES_INFERENCE_MODEL:-${LAMARK_MODEL:-lamark-cycle3}}"

VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: Lamark venv not found at $VENV_PY"
    echo "Run \`lamark setup\` first."
    exit 1
fi

# Endpoint health check — Hermes will fail less politely if vLLM is down.
if ! curl -fsS -m 3 "${LM_BASE_URL}/models" >/dev/null 2>&1; then
    echo "ERROR: model server not responding at ${LM_BASE_URL}"
    echo "  - start the local server with: lamark serve start"
    echo "  - or check that your remote endpoint is reachable"
    exit 2
fi

echo "Lamark — connected to ${LM_BASE_URL}"
curl -fsS "${LM_BASE_URL}/models" | python3 -c "import json,sys; [print(' -',m['id']) for m in json.load(sys.stdin)['data']]" 2>/dev/null || true
echo "Default model: ${HERMES_INFERENCE_MODEL}"
echo ""

cd "$LAMARK_REPO"
PYTHONPATH="$LAMARK_REPO/vendor/hermes:$LAMARK_REPO/src" exec "$VENV_PY" -c "
import sys
from hermes_cli.main import main
sys.exit(main() or 0)
" "$@"
