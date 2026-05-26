#!/bin/bash
# `lamark train [--now|--status|--config]` — control nightly retrain pipeline.
#
#   lamark train --now            run scripts/lamark-nightly-train.sh immediately,
#                                 respecting the training.min_pairs threshold
#   lamark train --now --force    same, but bypass the threshold (always train)
#   lamark train --status         show last run, next scheduled, pending pairs
#   lamark train --config         show current training config (frequency + threshold)
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"

ACTION="${1:---now}"
FORCE=0
if [ "${2:-}" = "--force" ] || [ "${1:-}" = "--force" ]; then
    FORCE=1
fi

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$*"; }

cmd_status() {
    echo "Lamark training status"
    echo "======================"
    # Config
    if [ -f "$HERMES_HOME/config.yaml" ]; then
        "$VENV_PY" -c "
import yaml
try:
    cfg = yaml.safe_load(open(r'$HERMES_HOME/config.yaml').read()) or {}
    t = cfg.get('training') or {}
    print(f'  frequency:  {t.get(\"frequency\", \"manual\")}')
    print(f'  min_pairs:  {t.get(\"min_pairs\", 50)}')
except Exception as e:
    print(f'  (config read error: {e})')
"
    fi

    # Pending pairs in archive (rough — count incoming/*.jsonl lines)
    if [ -d "$LAMARK_HOME/archive/incoming" ]; then
        local pending; pending=$(find "$LAMARK_HOME/archive/incoming" -name '*.jsonl' -exec cat {} + 2>/dev/null | wc -l | tr -d ' ')
        ok "Pending pairs in incoming/: $pending"
    fi

    # Last run from train-history.jsonl
    if [ -f "$LAMARK_HOME/train-history.jsonl" ]; then
        echo ""
        echo "Last 5 runs (newest first):"
        tail -5 "$LAMARK_HOME/train-history.jsonl" | tac | "$VENV_PY" -c "
import json, sys
for line in sys.stdin:
    try:
        r = json.loads(line)
        action = r.get('action','?')
        ts = r.get('ts','?')
        n = r.get('n_pairs','?')
        loss = r.get('final_loss')
        loss_s = f'  loss={loss:.3f}' if isinstance(loss,(int,float)) else ''
        print(f'  {ts}  {action:<8}  n_pairs={n}{loss_s}')
    except Exception:
        pass
"
    else
        warn "No training history yet."
    fi

    # Next scheduled (systemd timer)
    local user_name="${SUDO_USER:-$USER}"
    if systemctl list-timers --all 2>/dev/null | grep -q "lamark-nightly@${user_name}"; then
        local next; next=$(systemctl list-timers "lamark-nightly@${user_name}.timer" --no-pager --no-legend 2>/dev/null | awk '{print $1, $2}')
        ok "Next scheduled: $next"
    else
        warn "Timer not installed. Run \`lamark setup\` to enable scheduled retrain."
    fi
}

cmd_config() {
    if [ ! -f "$HERMES_HOME/config.yaml" ]; then
        warn "No config.yaml yet. Run \`lamark setup\`."
        exit 0
    fi
    "$VENV_PY" -c "
import yaml
cfg = yaml.safe_load(open(r'$HERMES_HOME/config.yaml').read()) or {}
t = cfg.get('training') or {}
print('frequency: ', t.get('frequency', 'manual'))
print('min_pairs: ', t.get('min_pairs', 50))
print()
print('Edit via: lamark config set training.frequency weekly')
print('          lamark config set training.min_pairs 100')
"
}

cmd_now() {
    if [ ! -x "$LAMARK_REPO/scripts/lamark-nightly-train.sh" ]; then
        chmod +x "$LAMARK_REPO/scripts/lamark-nightly-train.sh"
    fi
    if [ "$FORCE" = "1" ]; then
        echo "[lamark train] running with --force (ignoring threshold)"
        LAMARK_TRAIN_FORCE=1 exec "$LAMARK_REPO/scripts/lamark-nightly-train.sh"
    else
        exec "$LAMARK_REPO/scripts/lamark-nightly-train.sh"
    fi
}

case "$ACTION" in
    --now|now)     cmd_now ;;
    --status|status)   cmd_status ;;
    --config|config)   cmd_config ;;
    --force)       FORCE=1; cmd_now ;;
    -h|--help|help)
        cat <<EOF
Usage: lamark train [--now|--status|--config] [--force]

  --now        Run the retrain pipeline immediately (respects threshold).
  --now --force  Same, but train even if below the threshold.
  --status     Show last run, next scheduled, pending pair count.
  --config     Show current training frequency + threshold.

To change settings:
  lamark config set training.frequency weekly
  lamark config set training.min_pairs 100
EOF
        ;;
    *)
        echo "Usage: lamark train [--now|--status|--config] [--force]"
        exit 1 ;;
esac
