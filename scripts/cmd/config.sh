#!/bin/bash
# `lamark config {get|set|show} [key] [value]` — edit Hermes-home config.yaml.
#
# Lightweight wrapper around yaml read/write. Keys use dotted paths:
#   lamark config set training.frequency weekly
#   lamark config set training.min_pairs 100
#   lamark config get model.default
#   lamark config show                 # print whole config
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"
CFG="$HERMES_HOME/config.yaml"

ACTION="${1:-show}"

if [ ! -f "$CFG" ]; then
    echo "ERROR: $CFG not found. Run \`lamark setup\` first."
    exit 1
fi

case "$ACTION" in
    show|--show)
        cat "$CFG"
        ;;
    get|--get)
        KEY="${2:-}"
        [ -n "$KEY" ] || { echo "Usage: lamark config get <key>"; exit 1; }
        "$VENV_PY" -c "
import yaml
d = yaml.safe_load(open(r'$CFG').read()) or {}
for part in '$KEY'.split('.'):
    if isinstance(d, dict):
        d = d.get(part)
    else:
        d = None
        break
print(d if d is not None else '(unset)')
"
        ;;
    set|--set)
        KEY="${2:-}"
        VAL="${3:-}"
        [ -n "$KEY" ] && [ -n "$VAL" ] || { echo "Usage: lamark config set <key> <value>"; exit 1; }
        "$VENV_PY" -c "
import yaml
from pathlib import Path
p = Path(r'$CFG')
cfg = yaml.safe_load(p.read_text()) or {}

# Walk the dotted path, creating intermediate dicts as needed
parts = '$KEY'.split('.')
node = cfg
for part in parts[:-1]:
    if part not in node or not isinstance(node.get(part), dict):
        node[part] = {}
    node = node[part]

# Coerce value to int if it looks like one (so YAML stays well-typed)
val = '$VAL'
try:
    val_typed = int(val)
except ValueError:
    if val.lower() in ('true', 'false'):
        val_typed = val.lower() == 'true'
    else:
        val_typed = val
node[parts[-1]] = val_typed

p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print(f'set $KEY = {val_typed!r}')
"
        ;;
    *)
        cat <<EOF
Usage: lamark config {show|get|set} [key] [value]

Examples:
  lamark config show
  lamark config get training.min_pairs
  lamark config set training.min_pairs 100
  lamark config set training.frequency weekly
EOF
        exit 1 ;;
esac
