#!/bin/bash
# `lamark setup` — interactive first-time wizard.
#
# Three branches:
#   1) Local (recommended)      — download model + run vLLM on this machine
#   2) Existing endpoint        — point at an already-running OpenAI-compatible URL
#   3) Cloud-first              — use cloud provider now, optionally download local in background
#
# Writes its decisions to:
#   $HERMES_HOME/config.yaml    — model+provider+base_url
#   $HERMES_HOME/env            — LM_BASE_URL, LM_API_KEY, HERMES_INFERENCE_*
#   $LAMARK_HOME/setup.json     — chosen branch, timestamps, recommended_model
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"

mkdir -p "$HERMES_HOME" "$LAMARK_HOME"

err()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN:  $*" >&2; }
note() { echo "[lamark] $*"; }

if [ ! -x "$VENV_PY" ]; then
    err "Lamark venv not found at $VENV_PY. Run scripts/setup_spark.sh once to provision the host."
fi

# Run hardware detection up front — used by branches 1 and 3.
# `hardware.py` now always exits 0 and emits a single JSON blob on stdout.
# We still defend against parse failures (host might lack pyyaml, etc).
HW_JSON=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -m lamark.hardware 2>/dev/null)
if ! echo "$HW_JSON" | "$VENV_PY" -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    HW_JSON='{"hardware":{"gpu_name":null,"effective_memory_gb":0},"tier":"NONE","recommended_model":null}'
fi

_pick() {
    # _pick <python expression that returns a string for `d` = parsed JSON>
    echo "$HW_JSON" | "$VENV_PY" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print($1)
except Exception:
    print('')
" 2>/dev/null
}
TIER=$(_pick "d.get('tier','NONE') or 'NONE'")
RECOMMENDED_MODEL=$(_pick "d.get('recommended_model') or ''")
GPU_NAME=$(_pick "(d.get('hardware') or {}).get('gpu_name') or 'CPU only'")
EFF_MEM=$(_pick "int((d.get('hardware') or {}).get('effective_memory_gb') or 0)")
[ -z "$TIER" ] && TIER="NONE"
[ -z "$EFF_MEM" ] && EFF_MEM=0

cat <<EOF
==================================================
  Lamark — locally-hosted personal AI agent
==================================================

Detected hardware: ${GPU_NAME} · ${EFF_MEM} GB effective memory · tier ${TIER}
EOF
if [ -n "$RECOMMENDED_MODEL" ]; then
    echo "Recommended local model: ${RECOMMENDED_MODEL}"
fi

cat <<EOF

How do you want to run inference?
  [1] Local (recommended)
      Lamark downloads and serves a model on YOUR hardware. Privacy by default.
      Needs: ~60 GB disk (tier S) / ~20 GB (M) / ~15 GB (L). First run ~30 min.

  [2] Existing endpoint
      You already run ollama / vLLM / LM Studio / TGI. Lamark just connects.
      Needs: OpenAI-compatible /v1 URL + optional API key.

  [3] Cloud-first
      Start with a cloud provider (Claude, GPT, OpenRouter). Local model
      downloads in background; switch over later when ready.
      Needs: API key for one cloud provider.

EOF

read -rp "Choice [1]: " CHOICE
CHOICE="${CHOICE:-1}"

CONFIG_FILE="$HERMES_HOME/config.yaml"
ENV_FILE="$HERMES_HOME/env"
SETUP_FILE="$LAMARK_HOME/setup.json"

write_local_config() {
    local model="$1"
    cat > "$CONFIG_FILE" <<YAML
# Lamark — local inference via vLLM on port 8000.
model:
  default: ${model}
  provider: lm-studio
  base_url: http://127.0.0.1:8000/v1

model_aliases:
  ${model}:
    model: ${model}
    provider: lm-studio
    base_url: http://127.0.0.1:8000/v1
YAML
    cat > "$ENV_FILE" <<EOF
export LM_API_KEY=not-needed
export LM_BASE_URL=http://127.0.0.1:8000/v1
export HERMES_INFERENCE_PROVIDER=lm-studio
export HERMES_INFERENCE_MODEL=${model}
EOF
}

write_endpoint_config() {
    local url="$1"; local key="$2"; local model_name="$3"
    cat > "$CONFIG_FILE" <<YAML
# Lamark — using user-supplied OpenAI-compatible endpoint.
model:
  default: ${model_name}
  provider: lm-studio
  base_url: ${url}

model_aliases:
  ${model_name}:
    model: ${model_name}
    provider: lm-studio
    base_url: ${url}
YAML
    cat > "$ENV_FILE" <<EOF
export LM_API_KEY=${key:-not-needed}
export LM_BASE_URL=${url}
export HERMES_INFERENCE_PROVIDER=lm-studio
export HERMES_INFERENCE_MODEL=${model_name}
EOF
}

case "$CHOICE" in
    1)
        # ---------------- BRANCH 1: Local ----------------
        if [ "$TIER" = "NONE" ] || [ -z "$RECOMMENDED_MODEL" ]; then
            err "No suitable local model for this hardware. Try option [2] or [3]."
        fi
        MODEL_NAME="${LAMARK_MODEL:-$RECOMMENDED_MODEL}"
        note "Selected model: $MODEL_NAME"

        # Warn if the picked model entry is not yet `tested: true` in the
        # registry. We only have continuous verification for tier S (Spark);
        # tiers M/L/XS work in theory but haven't been validated on real
        # hardware in this release.
        IS_TESTED=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
from lamark.registry import load_registry
reg = load_registry()
e = reg.get('$MODEL_NAME')
print('true' if (e and getattr(e, 'tested', False)) else 'false')
" 2>/dev/null || echo "false")
        if [ "$IS_TESTED" != "true" ]; then
            echo ""
            warn "Model '$MODEL_NAME' is marked experimental (untested on real hardware)."
            warn "This is Phase 1 alpha: only the DGX Spark / tier-S path has been verified."
            warn "On consumer GPUs (4090/3090/Mac) you may hit Spark-specific quirks."
            warn "Report issues at github.com/Merocle/lamark-agent/issues."
            echo ""
            read -rp "Continue with experimental model? [y/N] " AGREE
            AGREE="${AGREE:-n}"
            if [ "${AGREE,,}" != "y" ]; then
                err "Aborted. Try option [2] (existing endpoint) or [3] (cloud-first) instead."
            fi
        fi

        HF_ID=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
from lamark.registry import get_model
print(get_model('$MODEL_NAME').hf_id)
")
        DOWNLOAD_DIR="$LAMARK_HOME/models/hf/$(echo "$HF_ID" | tr '/' '_')"

        if [ -d "$DOWNLOAD_DIR" ] && [ -f "$DOWNLOAD_DIR/config.json" ]; then
            note "Model already on disk at $DOWNLOAD_DIR — skipping download."
        else
            note "Downloading $HF_ID (this can take 15-60 minutes depending on size + network)..."
            "$VENV_PY" -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_ID', local_dir=r'$DOWNLOAD_DIR', max_workers=8)
"
        fi

        # Build Lamark chat template (bakes IDENTITY_PROMPT into tokenizer template).
        PATCHED_TPL="$DOWNLOAD_DIR/lamark_chat_template.jinja"
        if [ ! -f "$PATCHED_TPL" ] && [ -f "$DOWNLOAD_DIR/chat_template.jinja" ]; then
            note "Baking Lamark identity into chat template..."
            PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -m lamark.templates.build_template \
                --input "$DOWNLOAD_DIR/chat_template.jinja" \
                --output "$PATCHED_TPL"
        fi

        write_local_config "$MODEL_NAME"
        note "Local config written → $CONFIG_FILE"
        note "Next: \`lamark serve start\` to launch the model server."
        SETUP_BRANCH="local"
        ;;

    2)
        # ---------------- BRANCH 2: Existing endpoint ----------------
        read -rp "Endpoint URL (e.g. http://127.0.0.1:11434/v1 for ollama): " EP_URL
        [ -n "$EP_URL" ] || err "URL is required."
        read -rp "API key (blank for none): " EP_KEY || true
        read -rp "Model name to use (e.g. llama3.1:8b, gpt-oss-20b): " EP_MODEL
        [ -n "$EP_MODEL" ] || err "Model name is required."

        note "Probing $EP_URL ..."
        if ! curl -fsS -m 5 ${EP_KEY:+-H "Authorization: Bearer $EP_KEY"} "${EP_URL%/}/models" >/dev/null 2>&1; then
            echo "WARNING: probe failed. Continuing anyway — fix the endpoint then run \`lamark chat\`."
        fi

        write_endpoint_config "$EP_URL" "$EP_KEY" "$EP_MODEL"
        note "Endpoint config written → $CONFIG_FILE"
        SETUP_BRANCH="existing-endpoint"
        ;;

    3)
        # ---------------- BRANCH 3: Cloud-first ----------------
        note "Cloud-first setup. Lamark will use the cloud provider for chat now,"
        note "and start a background download of a tier-appropriate local model."
        echo ""
        note "Delegating to Hermes provider wizard..."
        PYTHONPATH="$LAMARK_REPO/vendor/hermes:$LAMARK_REPO/src" \
            HERMES_HOME="$HERMES_HOME" \
            "$VENV_PY" -c "from hermes_cli.main import main; main()" model

        # Background-download the local model if hardware supports any tier.
        if [ -n "$RECOMMENDED_MODEL" ] && [ "$TIER" != "NONE" ]; then
            HF_ID=$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
from lamark.registry import get_model
print(get_model('$RECOMMENDED_MODEL').hf_id)
")
            DOWNLOAD_DIR="$LAMARK_HOME/models/hf/$(echo "$HF_ID" | tr '/' '_')"
            note "Kicking off background download of $HF_ID → $DOWNLOAD_DIR"
            note "(check progress: \`lamark status\` or \`lamark logs download\`)"
            mkdir -p "$LAMARK_HOME/logs"
            nohup "$VENV_PY" -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_ID', local_dir=r'$DOWNLOAD_DIR', max_workers=4)
" >"$LAMARK_HOME/logs/download.log" 2>&1 &
            echo "$!" > "$LAMARK_HOME/download.pid"
        fi
        SETUP_BRANCH="cloud-first"
        ;;

    *)
        err "Unknown choice: $CHOICE"
        ;;
esac

# ============================================================
# Training trigger: how often + threshold
# ============================================================
# Only meaningful when there's local GPU hardware to run the retrain on.
# For the Existing-endpoint branch (user points at remote vLLM), and on
# CPU-only hosts (Macs, headless boxes), the retrain has to happen
# wherever the model actually lives — not on this client.
if [ "$SETUP_BRANCH" = "existing-endpoint" ] || [ "$TIER" = "NONE" ]; then
    note "Skipping training-trigger setup: no local GPU on this host."
    note "Retraining happens on the machine that serves the model."
    note "(Set training.frequency + min_pairs there via \`lamark config set\`.)"
    SKIP_TRAINING_TRIGGER=1
fi

if [ "${SKIP_TRAINING_TRIGGER:-0}" != "1" ]; then
echo ""
echo "============================================"
echo "  How often should Lamark retrain on your data?"
echo "============================================"
echo ""
echo "  Lamark accumulates chat-derived training pairs in $LAMARK_HOME/archive/."
echo "  Retraining bakes those pairs into a fresh LoRA adapter (1-10 min on tier S)."
echo ""
echo "  [1] Daily       (timer fires nightly at ~03:17, retrain if enough new pairs)"
echo "  [2] Weekly      (Sunday night)"
echo "  [3] Manual only (you run \`lamark train --now\` when you want)"
echo ""
read -rp "Choice [1]: " TFREQ
TFREQ="${TFREQ:-1}"
case "$TFREQ" in
    1) TRAIN_FREQ="daily";    SYSTEMD_CAL="*-*-* 03:17:00" ;;
    2) TRAIN_FREQ="weekly";   SYSTEMD_CAL="Sun 03:17:00" ;;
    3) TRAIN_FREQ="manual";   SYSTEMD_CAL="" ;;
    *) TRAIN_FREQ="daily";    SYSTEMD_CAL="*-*-* 03:17:00" ;;
esac
note "Frequency: $TRAIN_FREQ"

echo ""
read -rp "Minimum new pairs to actually retrain? [50]: " TMIN
TMIN="${TMIN:-50}"
# sanity-coerce
if ! [[ "$TMIN" =~ ^[0-9]+$ ]]; then
    warn "Not a number, defaulting to 50."
    TMIN=50
fi
note "Threshold: retrain only when >= $TMIN new pairs accumulated"

# Persist into config.yaml
"$VENV_PY" -c "
import yaml
from pathlib import Path
p = Path(r'$CONFIG_FILE')
cfg = yaml.safe_load(p.read_text()) if p.is_file() else {}
cfg.setdefault('training', {})
cfg['training']['frequency'] = '$TRAIN_FREQ'
cfg['training']['min_pairs'] = int('$TMIN')
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
"

# Install systemd timer if frequency != manual and we have sudo
if [ "$TRAIN_FREQ" != "manual" ]; then
    chmod +x "$LAMARK_REPO/scripts/lamark-nightly-train.sh" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1 && (sudo -n true 2>/dev/null); then
        USER_NAME="${SUDO_USER:-$USER}"
        SYS_DIR="/etc/systemd/system"
        SVC="$SYS_DIR/lamark-nightly@${USER_NAME}.service"
        TMR="$SYS_DIR/lamark-nightly@${USER_NAME}.timer"

        sed "s/%i/${USER_NAME}/g" "$LAMARK_REPO/scripts/systemd/lamark-nightly.service" | sudo tee "$SVC" >/dev/null
        # Override OnCalendar per chosen frequency
        sed "s/%i/${USER_NAME}/g; s|^OnCalendar=.*|OnCalendar=${SYSTEMD_CAL}|" \
            "$LAMARK_REPO/scripts/systemd/lamark-nightly.timer" | sudo tee "$TMR" >/dev/null
        sudo systemctl daemon-reload
        sudo systemctl enable --now "lamark-nightly@${USER_NAME}.timer" >/dev/null 2>&1 || true
        note "Systemd timer enabled: $SYSTEMD_CAL"
    else
        warn "No sudo or no systemctl — timer not installed. Run \`lamark train --now\` manually,"
        warn "or copy scripts/systemd/lamark-nightly.* to ~/.config/systemd/user/ yourself."
    fi
fi
fi   # end SKIP_TRAINING_TRIGGER

# Record the branch choice for `lamark status` and future migrations.
cat > "$SETUP_FILE" <<EOF
{
  "branch": "${SETUP_BRANCH}",
  "tier": "${TIER}",
  "hardware_gpu": "${GPU_NAME}",
  "effective_memory_gb": ${EFF_MEM},
  "recommended_model": "${RECOMMENDED_MODEL}",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo ""
echo "==================================================="
note "Setup complete (branch: ${SETUP_BRANCH})."
echo "==================================================="
echo ""
echo "Try:"
echo "  lamark serve start    # if you chose Local"
echo "  lamark chat           # open the REPL"
echo "  lamark status         # health overview"
