#!/bin/bash
# `lamark gateway {run|start|stop|status|setup|...}` — messaging gateway control.
#
# Sources $HERMES_HOME/env so LM_BASE_URL / LM_API_KEY / HERMES_INFERENCE_*
# actually reach the gateway daemon. Without this, a `--replace` daemon
# launched from a fresh shell ends up with no provider env vars and falls
# back to the lm-studio default (http://127.0.0.1:1234/v1), failing
# every API call.
#
# Forwards all args to `hermes-agent gateway <args>`.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
export HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"

# Source the env file written by `lamark setup` — same logic as chat.sh.
ENV_FILE="$HERMES_HOME/env"
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

# Defaults in case the env file doesn't exist (e.g. user hand-rolled config).
export LM_API_KEY="${LM_API_KEY:-not-needed}"
export LM_BASE_URL="${LM_BASE_URL:-http://127.0.0.1:8000/v1}"
export HERMES_INFERENCE_PROVIDER="${HERMES_INFERENCE_PROVIDER:-lm-studio}"
export HERMES_INFERENCE_MODEL="${HERMES_INFERENCE_MODEL:-${LAMARK_MODEL:-qwen-base}}"

VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: Lamark venv not found at $VENV_PY"
    echo "Run scripts/install.sh first."
    exit 1
fi

cd "$LAMARK_REPO"
PYTHONPATH="$LAMARK_REPO/vendor/hermes:$LAMARK_REPO/src" exec "$VENV_PY" -c "
import sys
from hermes_cli.main import main
sys.exit(main() or 0)
" gateway "$@"
