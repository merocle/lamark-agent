#!/bin/bash
# `lamark train [--now|--status|--config|--schedule]` — control nightly retrain pipeline.
#
#   lamark train --now            run scripts/lamark-nightly-train.sh immediately,
#                                 respecting the training.min_pairs threshold
#   lamark train --now --force    same, but bypass the threshold (always train)
#   lamark train --status         show last run, next scheduled, pending pairs
#   lamark train --config         show current training config (frequency + threshold)
#   lamark train --schedule       show current systemd timer schedule + next firing
#   lamark train --schedule "<spec>"   install/update systemd timer with given OnCalendar value
#                                 (e.g. "*-*-* 03:00:00" for nightly 3am)
#   lamark train --schedule off   disable the timer (keeps unit files for easy re-enable)
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

    # New (unconsumed) qualifying pairs — the SAME count the run threshold
    # uses (not the cumulative archive). Falls back to a raw incoming line
    # count if curation isn't importable here.
    if [ -d "$LAMARK_HOME/archive/incoming" ]; then
        local newpairs
        newpairs=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" - <<'PY' 2>/dev/null
import os
try:
    from lamark.archive import Archive
    from lamark.train.curation import count_new_pairs
    print(count_new_pairs(Archive.open(os.path.join(os.environ["LAMARK_HOME"], "archive"))))
except Exception:
    print("?")
PY
)
        if [ "${newpairs:-?}" != "?" ]; then
            ok "New (unconsumed) pairs since last promote: $newpairs"
        else
            local pending; pending=$(find "$LAMARK_HOME/archive/incoming" -name '*.jsonl' -exec cat {} + 2>/dev/null | wc -l | tr -d ' ')
            ok "Pending pairs in incoming/: $pending"
        fi
    fi

    # Last run from train-history.jsonl. New-schema records carry
    # new_pairs/cumulative_pairs/gate_probes; old records carry n_pairs.
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
        # Prefer the new fields; fall back to the legacy n_pairs.
        new = r.get('new_pairs')
        cum = r.get('cumulative_pairs')
        if new is not None or cum is not None:
            pairs_s = f'new={new if new is not None else \"?\"} cum={cum if cum is not None else \"?\"}'
        else:
            pairs_s = f'n_pairs={r.get(\"n_pairs\", \"?\")}'
        loss = r.get('final_loss')
        loss_s = f'  loss={loss:.3f}' if isinstance(loss,(int,float)) else ''
        probes = r.get('gate_probes')
        probes_s = f'  gate[{probes}]' if probes else ''
        print(f'  {ts}  {action:<9}  {pairs_s}{loss_s}{probes_s}')
    except Exception:
        pass
"
    else
        warn "No training history yet."
    fi

    # Next scheduled (systemd timer — user-scope, installed by `lamark train --schedule`)
    if systemctl --user list-timers --all 2>/dev/null | grep -q "lamark-trainer.timer"; then
        local next; next=$(systemctl --user list-timers lamark-trainer.timer --no-pager --no-legend 2>/dev/null | awk '{print $1, $2}')
        ok "Next scheduled: $next"
    else
        warn "Timer not installed. Run \`lamark train --schedule \"*-*-* 03:00:00\"\` to enable nightly retrain."
    fi
}

# `lamark train --schedule [spec]` — manage the systemd --user timer that
# fires `lamark-nightly-train.sh`. Three modes:
#   no arg          → show current OnCalendar + next firing + active state
#   off             → disable + stop the timer (unit files preserved)
#   <OnCalendar>    → install/update timer with this systemd OnCalendar spec
#                     (any systemd-valid value, e.g. "*-*-* 03:00:00" for
#                     nightly 3am, "Sun *-*-* 04:30:00" for weekly Sun 04:30)
cmd_schedule() {
    local spec="${1:-}"
    local timer_path="$HOME/.config/systemd/user/lamark-trainer.timer"
    local service_path="$HOME/.config/systemd/user/lamark-trainer.service"

    if [ -z "$spec" ]; then
        echo "Lamark trainer schedule"
        echo "======================="
        if [ -f "$timer_path" ]; then
            local cal; cal=$(grep '^OnCalendar=' "$timer_path" | head -1 | cut -d= -f2-)
            ok "OnCalendar: $cal"
            local active; active=$(systemctl --user is-active lamark-trainer.timer 2>/dev/null)
            local enabled; enabled=$(systemctl --user is-enabled lamark-trainer.timer 2>/dev/null)
            ok "State: $active (enabled-at-boot: $enabled)"
            systemctl --user list-timers lamark-trainer.timer --no-pager 2>/dev/null | head -3
        else
            warn "Not configured. Install with: lamark train --schedule \"*-*-* 03:00:00\""
        fi
        return 0
    fi

    if [ "$spec" = "off" ]; then
        if [ -f "$timer_path" ]; then
            systemctl --user disable --now lamark-trainer.timer 2>&1 | tail -3
            ok "Schedule disabled. Re-enable with: lamark train --schedule \"<OnCalendar>\""
        else
            warn "No timer installed; nothing to disable."
        fi
        return 0
    fi

    # Install / update the unit files. Use systemd %h for portability across
    # users (Linger means service may run with a different cwd than current
    # shell), and absolute repo path via LAMARK_REPO for the ExecStart so
    # users who clone the repo elsewhere don't have to edit the unit.
    mkdir -p "$(dirname "$timer_path")"
    cat > "$service_path" <<EOF
[Unit]
Description=Lamark nightly trainer — adapt LoRA from accumulated pairs
After=network-online.target

[Service]
Type=oneshot
ExecStart=$LAMARK_REPO/scripts/lamark-nightly-train.sh
EnvironmentFile=-%h/.lamark/hermes-home/.env
EnvironmentFile=-%h/.lamark/hermes-home/env
Environment="LAMARK_HOME=%h/.lamark"
Environment="LAMARK_REPO=$LAMARK_REPO"
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

    cat > "$timer_path" <<EOF
[Unit]
Description=Lamark nightly trainer timer

[Timer]
OnCalendar=$spec
Persistent=true
Unit=lamark-trainer.service

[Install]
WantedBy=timers.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now lamark-trainer.timer 2>&1 | tail -3
    ok "Schedule set: $spec"
    systemctl --user list-timers lamark-trainer.timer --no-pager 2>/dev/null | head -3
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
    --schedule|schedule)
        # Pass second arg (OnCalendar spec or "off") through; empty means status.
        cmd_schedule "${2:-}" ;;
    --force)       FORCE=1; cmd_now ;;
    -h|--help|help)
        cat <<EOF
Usage: lamark train [--now|--status|--config|--schedule] [--force]

  --now              Run the retrain pipeline immediately (respects threshold).
  --now --force      Same, but train even if below the threshold.
  --status           Show last run, next scheduled, pending pair count.
  --config           Show current training frequency + threshold.
  --schedule         Show current systemd timer schedule + next firing.
  --schedule "<spec>"  Install/update the timer (OnCalendar systemd spec).
                     Examples:
                       "*-*-* 03:00:00"        nightly at 3am
                       "Sun *-*-* 04:30:00"    weekly, Sunday 04:30
                       "*-*-* 02,14:00:00"     twice daily, 2am & 2pm
  --schedule off     Disable the timer (preserves unit files).

To change threshold:
  lamark config set training.min_pairs 100
EOF
        ;;
    *)
        echo "Usage: lamark train [--now|--status|--config|--schedule] [--force]"
        exit 1 ;;
esac
