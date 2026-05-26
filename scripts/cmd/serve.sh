#!/bin/bash
# `lamark serve {start|stop|status|restart}` — manage the local vLLM model server.
#
# Resolves which model+adapters to serve from $HERMES_HOME/config.yaml and
# the model registry. Idempotent: `start` is a no-op if already running;
# `stop` succeeds whether or not anything was running.
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
LAMARK_REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
VENV_PY="${LAMARK_VENV:-$LAMARK_HOME/venv}/bin/python"

CONTAINER_NAME="lamark-vllm"
LOG_FILE="$LAMARK_HOME/logs/vllm.log"
mkdir -p "$LAMARK_HOME/logs"

ACTION="${1:-start}"

current_status() {
    docker ps --filter "name=$CONTAINER_NAME" --format "{{.Status}}" 2>/dev/null | head -1
}

cmd_status() {
    local st
    st="$(current_status)"
    if [ -n "$st" ]; then
        echo "vLLM container: $st"
        if curl -fsS -m 3 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
            echo "/v1/models reachable. Models:"
            curl -fsS http://127.0.0.1:8000/v1/models | python3 -c "import json,sys; [print(' -',m['id']) for m in json.load(sys.stdin)['data']]"
        else
            echo "Container is up but /v1/models not responding yet (model still loading?)."
        fi
    else
        echo "vLLM container: not running"
    fi
}

cmd_stop() {
    if [ -n "$(current_status)" ]; then
        docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
        echo "vLLM container stopped."
    else
        echo "vLLM container was not running."
    fi
}

cmd_start() {
    if [ -n "$(current_status)" ]; then
        echo "vLLM container already running. Use \`lamark serve restart\` to bounce it."
        cmd_status
        return 0
    fi

    # Resolve which model + LoRA adapters to serve.
    local model_name
    model_name="$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
import yaml, sys
from pathlib import Path
cfg = yaml.safe_load(Path(r'$HERMES_HOME/config.yaml').read_text())
m = cfg.get('model') or {}
print(m.get('default') or '')
")"
    [ -n "$model_name" ] || { echo "ERROR: no default model in $HERMES_HOME/config.yaml. Run \`lamark setup\`."; exit 2; }

    # Look up registry entry to get HF id + serving config.
    local registry_json
    registry_json="$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
import json
from lamark.registry import get_model
e = get_model('$model_name')
print(json.dumps({
    'hf_id': e.hf_id,
    'max_model_len': e.serving.max_model_len,
    'expert_parallel': e.serving.expert_parallel,
    'tool_call_parser': e.serving.tool_call_parser or '',
}))
")"
    local hf_id max_len ep tcp
    hf_id="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['hf_id'])")"
    max_len="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['max_model_len'])")"
    ep="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['expert_parallel'])")"
    tcp="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['tool_call_parser'])")"
    local local_dir="$LAMARK_HOME/models/hf/$(echo "$hf_id" | tr '/' '_')"
    [ -d "$local_dir" ] || { echo "ERROR: model not downloaded at $local_dir. Run \`lamark setup\`."; exit 3; }

    # Optional patched template.
    local tpl_flag=""
    if [ -f "$local_dir/lamark_chat_template.jinja" ]; then
        tpl_flag="--chat-template /lamark/models/hf/$(echo "$hf_id" | tr '/' '_')/lamark_chat_template.jinja"
    fi
    # Fallback: template patched out-of-tree (some Spark setups symlink
    # the model into a read-only NAS cache, so the template lives at
    # $LAMARK_HOME/models/templates/lamark_chat_template.jinja).
    if [ -z "$tpl_flag" ] && [ -f "$LAMARK_HOME/models/templates/lamark_chat_template.jinja" ]; then
        tpl_flag="--chat-template /lamark/models/templates/lamark_chat_template.jinja"
    fi

    # Optional MoE flag.
    local ep_flag=""
    if [ "$ep" = "True" ]; then ep_flag="--enable-expert-parallel"; fi

    # Optional tool-call parser.
    local tcp_flag=""
    if [ -n "$tcp" ] && [ "$tcp" != "None" ]; then
        tcp_flag="--enable-auto-tool-choice --tool-call-parser $tcp"
    fi

    # LoRA adapter discovery: every subdir of $LAMARK_HOME/adapters/ with an
    # adapter_config.json gets exposed as a serving model.
    local lora_flags=""
    if [ -d "$LAMARK_HOME/adapters" ]; then
        local lora_list=""
        for d in "$LAMARK_HOME/adapters"/*/; do
            [ -d "$d" ] || continue
            [ -f "$d/adapter_config.json" ] || continue
            local name; name="$(basename "$d")"
            lora_list="$lora_list $name=/lamark/adapters/$name"
        done
        if [ -n "$lora_list" ]; then
            local n_loras; n_loras="$(ls -1 "$LAMARK_HOME/adapters" | wc -l | tr -d ' ')"
            lora_flags="--enable-lora --max-lora-rank 16 --max-loras $n_loras --lora-modules$lora_list"
        fi
    fi

    echo "Starting vLLM container: $hf_id (max_len=$max_len)"
    [ -n "$lora_flags" ] && echo "  LoRA: $lora_flags"

    # Image: upstream vllm/vllm-openai:v0.21.0 (multi-arch arm64+amd64).
    # Verified 2026-05-25 to support qwen3_5_moe + LoRA + tool calling on
    # DGX Spark. Allow override via $LAMARK_VLLM_IMAGE if needed (e.g. pin
    # a future version or fall back to a tested older release).
    local image="${LAMARK_VLLM_IMAGE:-vllm/vllm-openai:v0.21.0}"

    # The model dir on host: $LAMARK_HOME/models/hf/<flattened_hf_id>.
    # On Spark the symlink may point into a shared NAS — mount /mnt:ro if
    # that's where the resolved path leads, so the symlink chase succeeds
    # inside the container.
    local host_model_dir="$LAMARK_HOME/models/hf/$(echo "$hf_id" | tr '/' '_')"
    local resolved_model_dir; resolved_model_dir="$(readlink -f "$host_model_dir")"
    local nas_mount=""
    if [[ "$resolved_model_dir" == /mnt/* ]]; then
        nas_mount="-v /mnt:/mnt:ro"
    fi

    docker run -d --name "$CONTAINER_NAME" \
        --restart=unless-stopped --gpus all --ipc=host \
        --ulimit memlock=-1 --ulimit stack=67108864 --shm-size=16g \
        -p 8000:8000 \
        -e TORCH_CUDA_ARCH_LIST=12.1 \
        -e VLLM_USE_V1=1 \
        -v "$LAMARK_HOME:/lamark" \
        $nas_mount \
        -w /lamark \
        --entrypoint /bin/bash \
        "$image" \
        -c "pip uninstall -y flash-attn flash_attn 2>/dev/null; vllm serve $resolved_model_dir --tensor-parallel-size 1 $ep_flag --gpu-memory-utilization 0.85 --host 0.0.0.0 --port 8000 --trust-remote-code --max-model-len $max_len --served-model-name qwen-base $tcp_flag $tpl_flag $lora_flags" \
        > "$LOG_FILE" 2>&1

    echo "Container started. Tail log: lamark logs vllm"
    echo "vLLM warmup typically takes 3-7 minutes for the model to load + JIT-compile."
}

case "$ACTION" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; cmd_start ;;
    *)       echo "Usage: lamark serve {start|stop|status|restart}"; exit 1 ;;
esac
