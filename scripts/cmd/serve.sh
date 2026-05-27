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

    # Cleanup any exited container with the same name. `docker run` refuses
    # to reuse the name otherwise — found by the first non-author install
    # where the previous custom-built image left a stopped container behind.
    if docker ps -a --filter "name=^${CONTAINER_NAME}$" --format '{{.ID}}' | grep -q .; then
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
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

    # Look up registry entry to get HF id + serving config.  `model.default`
    # in config.yaml is sometimes the served-model alias (e.g. 'qwen-base')
    # rather than a registry slug; in that case fall back to the tier-S
    # default entry rather than crashing with KeyError.
    local registry_json
    registry_json="$(PYTHONPATH="$LAMARK_REPO/src" "$VENV_PY" -c "
import json
from lamark.registry import get_model, load_registry, ModelEntry

name = '$model_name'
entry = None
try:
    entry = get_model(name)
except Exception:
    for n, e in load_registry().items():
        if isinstance(e, ModelEntry) and e.tier == 'S' and e.default_for_tier:
            entry = e
            break
if entry is None:
    raise SystemExit(f'no registry entry matches model.default={name!r} and no tier-S default available')

print(json.dumps({
    'hf_id': entry.hf_id,
    'max_model_len': entry.serving.max_model_len,
    'expert_parallel': entry.serving.expert_parallel,
    'tool_call_parser': entry.serving.tool_call_parser or '',
    'gpu_memory_utilization': entry.serving.gpu_memory_utilization,
    'enable_prefix_caching': entry.serving.enable_prefix_caching,
    'enable_chunked_prefill': entry.serving.enable_chunked_prefill,
    'reasoning_parser': entry.serving.reasoning_parser or '',
    'speculative_model': entry.serving.speculative_model or '',
    'num_speculative_tokens': entry.serving.num_speculative_tokens,
}))
")"
    local hf_id max_len ep tcp gmu prefix_cache chunked_prefill reasoning spec_model spec_tokens
    hf_id="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['hf_id'])")"
    max_len="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['max_model_len'])")"
    ep="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['expert_parallel'])")"
    tcp="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['tool_call_parser'])")"
    gmu="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['gpu_memory_utilization'])")"
    prefix_cache="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['enable_prefix_caching'])")"
    chunked_prefill="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['enable_chunked_prefill'])")"
    reasoning="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['reasoning_parser'])")"
    spec_model="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['speculative_model'])")"
    spec_tokens="$(echo "$registry_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['num_speculative_tokens'])")"
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

    # Optional perf flags. Booleans come back from python as "True"/"False"
    # strings; treat any non-"True" value as off.
    local perf_flags=""
    if [ "$prefix_cache" = "True" ]; then
        perf_flags="$perf_flags --enable-prefix-caching"
    fi
    if [ "$chunked_prefill" = "True" ]; then
        perf_flags="$perf_flags --enable-chunked-prefill"
    fi
    if [ -n "$reasoning" ] && [ "$reasoning" != "None" ]; then
        perf_flags="$perf_flags --reasoning-parser $reasoning"
    fi

    # Optional speculative decoding (DFlash). The draft model is downloaded
    # by switch-base.sh/setup.sh into the same models/hf tree as the base,
    # so we map host → container path the same way. vLLM accepts a JSON
    # blob as the --speculative-config value; we single-quote it inside
    # the outer double-quoted -c string so the JSON's own double quotes
    # survive both bash layers.
    #
    # Also pin --max-num-batched-tokens explicitly when spec-decode is
    # on. vLLM's automatic compute of max_num_scheduled_tokens collapses
    # to 1024 once draft slots eat into the chunked-prefill default,
    # which turns into the warning:
    #
    #   "max_num_scheduled_tokens is set to 1024 ... This may lead to
    #    suboptimal performance."
    #
    # Observed on Spark: without this pin, DFlash made throughput WORSE
    # than plain FP8 (~48 vs ~52 tok/s) because draft overhead wasn't
    # amortized over enough scheduled tokens. 16384 gives the scheduler
    # plenty of headroom with num_speculative_tokens ≤ ~10.
    #
    # LoRA caveat: when LoRA adapters are present we still launch with
    # DFlash, but spec-decode + LoRA has known config conflicts (vLLM
    # #41523). Adapters are loaded conditionally above; if they break
    # the runtime, remove the adapters and the spec_flag still works.
    local spec_flag=""
    if [ -n "$spec_model" ] && [ "$spec_model" != "None" ] && [ "$spec_model" != "" ]; then
        local spec_flat_id; spec_flat_id="$(echo "$spec_model" | tr '/' '_')"
        local spec_container_dir="/lamark/models/hf/$spec_flat_id"
        spec_flag="--speculative-config '{\"method\":\"dflash\",\"model\":\"$spec_container_dir\",\"num_speculative_tokens\":$spec_tokens}' --max-num-batched-tokens 16384"
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

    # Model location: host path + container-internal path.
    # We always pass the CONTAINER path to `vllm serve` (host paths aren't
    # reachable inside the container). If the host path is a symlink that
    # resolves into /mnt (Spark with a NAS-mounted HF cache), we also mount
    # /mnt:/mnt:ro so the symlink chase succeeds inside the container.
    local flat_hf_id; flat_hf_id="$(echo "$hf_id" | tr '/' '_')"
    local host_model_dir="$LAMARK_HOME/models/hf/$flat_hf_id"
    local container_model_dir="/lamark/models/hf/$flat_hf_id"
    local resolved_model_dir; resolved_model_dir="$(readlink -f "$host_model_dir")"
    local nas_mount=""
    if [[ "$resolved_model_dir" == /mnt/* ]]; then
        nas_mount="-v /mnt:/mnt:ro"
    fi
    # Use `--restart=no` (not `unless-stopped`): a crash-looping container
    # spams the log and hides the real error. Surface the failure once and
    # let the operator decide.

    docker run -d --name "$CONTAINER_NAME" \
        --restart=no --gpus all --ipc=host \
        --ulimit memlock=-1 --ulimit stack=67108864 --shm-size=16g \
        -p 8000:8000 \
        -e TORCH_CUDA_ARCH_LIST=12.1 \
        -e VLLM_USE_V1=1 \
        -v "$LAMARK_HOME:/lamark" \
        $nas_mount \
        -w /lamark \
        --entrypoint /bin/bash \
        "$image" \
        -c "pip uninstall -y flash-attn flash_attn 2>/dev/null; vllm serve $container_model_dir --tensor-parallel-size 1 $ep_flag --gpu-memory-utilization $gmu --max-num-seqs 128 --host 0.0.0.0 --port 8000 --trust-remote-code --max-model-len $max_len --served-model-name $model_name qwen-base $tcp_flag $perf_flags $spec_flag $tpl_flag $lora_flags" \
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
