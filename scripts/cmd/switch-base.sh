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

# Update config.yaml — change default model + alias + custom_providers entry.
# Uses the `custom` provider (not `lm-studio`): the latter has a hardcoded
# fallback base_url that breaks when env vars aren't perfectly threaded
# through to the gateway daemon. `custom` reads base_url directly from
# this file on every API call, and the endpoint appears in `/model`
# pickers under Custom Models.
"$VENV_PY" -c "
import yaml
from pathlib import Path

# vLLM serves the model under the registry name on Spark, but the served
# alias is 'qwen-base' (see scripts/cmd/serve.sh --served-model-name).
# Clients call by the served name, so the alias and custom_provider
# model entry should both be 'qwen-base', while the user-facing
# default in config remains the human-readable registry slug.
SERVED_NAME = 'qwen-base'

p = Path(r'$HERMES_HOME/config.yaml')
cfg = yaml.safe_load(p.read_text()) if p.is_file() else {}

cfg.setdefault('model', {})
cfg['model']['default'] = SERVED_NAME
cfg['model']['provider'] = 'custom'
cfg['model']['base_url'] = 'http://127.0.0.1:8000/v1'

# Rebuild model_aliases — drop any prior lm-studio aliases for our model.
aliases = cfg.setdefault('model_aliases', {})
aliases[SERVED_NAME] = {
    'model': SERVED_NAME,
    'provider': 'custom',
    'base_url': 'http://127.0.0.1:8000/v1',
}

# Rebuild custom_providers — keep any non-Lamark entries the user added,
# replace the Lamark one (matched by base_url pointing at our vLLM).
existing = cfg.get('custom_providers') or []
keep = [
    p for p in existing
    if isinstance(p, dict)
    and 'lamark' not in (p.get('name') or '').lower()
]
keep.insert(0, {
    'name': 'lamark-local',
    'base_url': 'http://127.0.0.1:8000/v1',
    'api_key': 'not-needed',
    'models': [{
        'name': SERVED_NAME,
        'context_length': 131072,
        'transport': 'openai_chat',
    }],
})
cfg['custom_providers'] = keep

p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
print('config.yaml updated.')
"

# The env file is preserved as an extension point (for tool API keys
# like TAVILY_API_KEY). After the lm-studio → custom migration there's
# no per-model env var to rewrite here.

echo ""
echo "Default model switched to: $MODEL_NAME"
echo ""
echo "To activate:"
echo "  lamark serve restart    # reload model server with the new base"
echo "  lamark chat             # start using the new model"
