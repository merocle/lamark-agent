#!/bin/bash
# `lamark logs [vllm|nightly|download|chat]` — tail recent logs.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LOG_DIR="$LAMARK_HOME/logs"
WHICH="${1:-vllm}"
LINES="${2:-100}"

case "$WHICH" in
    vllm)
        F="$LOG_DIR/vllm.log"
        ;;
    nightly|train)
        # Pick the most recent nightly-train-*.log
        F=$(ls -t "$LOG_DIR"/nightly-train-*.log 2>/dev/null | head -1 || true)
        ;;
    download)
        F="$LOG_DIR/download.log"
        ;;
    chat|session)
        # Hermes writes session logs under hermes-home; pick the latest.
        F=$(ls -t "${HERMES_HOME:-$LAMARK_HOME/hermes-home}"/logs/*.log 2>/dev/null | head -1 || true)
        ;;
    *)
        echo "Usage: lamark logs [vllm|nightly|download|chat] [lines]"
        echo ""
        echo "Available log files:"
        ls -la "$LOG_DIR" 2>/dev/null | tail -n +2 | awk '{print "  "$NF}'
        exit 1
        ;;
esac

if [ -z "${F:-}" ] || [ ! -f "$F" ]; then
    echo "(no $WHICH log yet)"
    exit 0
fi

echo "==> $F  (last $LINES lines, ctrl-c to exit follow)"
tail -n "$LINES" -F "$F"
