#!/bin/bash
# `lamark switch-base <model>` — switch the default base model.
#
# Looks up the model in scripts/model-registry.yaml, optionally downloads
# the weights, bakes the Lamark chat template, and updates config.yaml so
# `lamark chat` will use the new model on next launch.
#
# Does NOT restart vLLM automatically — that would interrupt anything
# currently running. Tell the user to `lamark serve restart` after.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"

if [ $# -eq 0 ]; then
    echo "Usage: lamark switch-base <model-name>"
    echo ""
    echo "Available models:"
    PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
from lamark.registry import load_registry
for name, entry in load_registry().items():
    if hasattr(entry, 'hf_id'):
        print(f'  {name:<28} tier={entry.tier:<3} arch={entry.arch:<5} hf={entry.hf_id}')
"
    exit 1
fi

MODEL_NAME="$1"

# Look up HF id
HF_ID=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
try:
    from lamark.registry import get_model
    print(get_model('$MODEL_NAME').hf_id)
except KeyError as e:
    print('ERROR:', e)
" 2>&1)
if echo "$HF_ID" | grep -q "^ERROR"; then
    echo "Unknown model '$MODEL_NAME'. Run \`lamark switch-base\` with no args to see the catalog."
    exit 2
fi

DOWNLOAD_DIR="$LAMARK_HOME/models/hf/$(echo "$HF_ID" | tr '/' '_')"
if [ ! -f "$DOWNLOAD_DIR/config.json" ]; then
    echo "Downloading $HF_ID to $DOWNLOAD_DIR ..."
    "$VENV_PY" -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_ID', local_dir=r'$DOWNLOAD_DIR', max_workers=8)
"
fi

# Bake Lamark chat template into the new model
if [ -f "$DOWNLOAD_DIR/chat_template.jinja" ] && [ ! -f "$DOWNLOAD_DIR/lamark_chat_template.jinja" ]; then
    echo "Baking Lamark identity into the chat template..."
    PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -m lamark.templates.build_template \
        --input "$DOWNLOAD_DIR/chat_template.jinja" \
        --output "$DOWNLOAD_DIR/lamark_chat_template.jinja"
fi

# Update config.yaml — change default model + alias
"$VENV_PY" -c "
import yaml
from pathlib import Path
p = Path(r'$HERMES_HOME/config.yaml')
cfg = yaml.safe_load(p.read_text()) if p.is_file() else {}
cfg.setdefault('model', {})['default'] = '$MODEL_NAME'
cfg['model']['provider'] = 'lm-studio'
cfg['model']['base_url'] = 'http://127.0.0.1:8000/v1'
cfg.setdefault('model_aliases', {})['$MODEL_NAME'] = {
    'model': '$MODEL_NAME',
    'provider': 'lm-studio',
    'base_url': 'http://127.0.0.1:8000/v1',
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print('config.yaml updated.')
"

# Update env file
if [ -f "$HERMES_HOME/env" ]; then
    sed -i.bak "s|^export HERMES_INFERENCE_MODEL=.*|export HERMES_INFERENCE_MODEL=$MODEL_NAME|" "$HERMES_HOME/env"
    rm -f "$HERMES_HOME/env.bak"
fi

echo ""
echo "Default model switched to: $MODEL_NAME"
echo ""
echo "To activate:"
echo "  lamark serve restart    # reload model server with the new base"
echo "  lamark chat             # start using the new model"
