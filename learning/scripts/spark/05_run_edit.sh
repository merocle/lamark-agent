#!/usr/bin/env bash
# Apply L3 knowledge edits (ROME/MEMIT) to the Nemotron-30B base model on
# DGX Spark. Runs the editor inside the pytorch container with EasyEdit
# mounted in. The output is a fresh HF model directory ready for vLLM.
#
# Usage:
#   ./05_run_edit.sh                              # full pipeline
#   ./05_run_edit.sh --dry-run                    # plan only, no model load
#   METHOD=rome  ./05_run_edit.sh                 # single-fact updates
#   ONLY=lamark-vs-lamarck,lamark-self-id ./05_run_edit.sh  # subset
#
# Environment variables:
#   MODEL_ID          HF id of the base model            (default: NemotronH-30B-A3B)
#   MODEL_DIR         on-host path to the base model     (derived from MODEL_ID)
#   EDIT_TAG          appended to the output model dir   (default: lamark-facts-v1)
#   OUTPUT_DIR        where the edited model is written  (derived from MODEL_ID + EDIT_TAG)
#   HPARAMS           YAML path inside the repo          (default: configs/memit/nemotron-h-30b-a3b.yaml)
#   FACTS             JSONL path inside the repo         (default: data/lamark_facts.jsonl)
#   METHOD            rome | memit                       (default: memit)
#   ONLY              comma-separated fact ids subset    (default: all)
#   EASYEDIT_ROOT     on-host EasyEdit checkout          (default: ~/EasyEdit)
#   TRAIN_IMAGE       container with torch + transformers (default: nvcr.io/nvidia/pytorch:26.01-py3)

set -euo pipefail

MODEL_ID="${MODEL_ID:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16}"
MODEL_SLUG="$(printf '%s' "$MODEL_ID" | tr '/' '_')"
MODEL_DIR="${MODEL_DIR:-$HOME/.lamark/models/hf/$MODEL_SLUG}"
EDIT_TAG="${EDIT_TAG:-lamark-facts-v1}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/.lamark/models/edited/${MODEL_SLUG}__${EDIT_TAG}}"
HPARAMS="${HPARAMS:-learning/configs/memit/nemotron-h-30b-a3b.yaml}"
FACTS="${FACTS:-learning/data/lamark_facts.jsonl}"
METHOD="${METHOD:-memit}"
ONLY="${ONLY:-}"
EASYEDIT_ROOT="${EASYEDIT_ROOT:-$HOME/EasyEdit}"
TRAIN_IMAGE="${TRAIN_IMAGE:-nvcr.io/nvidia/pytorch:26.01-py3}"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"  # learning/

log() { printf "\033[1;34m[edit]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m[edit]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[edit]\033[0m %s\n" "$*" >&2; }

log "model      : $MODEL_ID"
log "model dir  : $MODEL_DIR"
log "output dir : $OUTPUT_DIR"
log "hparams    : $HPARAMS"
log "facts      : $FACTS"
log "method     : $METHOD"
log "easyedit   : $EASYEDIT_ROOT"
log "container  : $TRAIN_IMAGE"
[ "$DRY_RUN" -eq 1 ] && log "DRY RUN — model is not loaded"

if [ ! -d "$MODEL_DIR" ]; then
    err "base model not found: $MODEL_DIR"
    err "download it first (see 01_setup.sh)"
    exit 1
fi
if [ ! -d "$EASYEDIT_ROOT" ]; then
    err "EasyEdit checkout not found: $EASYEDIT_ROOT"
    err "git clone https://github.com/zjunlp/EasyEdit $EASYEDIT_ROOT"
    exit 1
fi
if [ ! -f "$REPO_DIR/$HPARAMS" ]; then
    err "hparams not found: $REPO_DIR/$HPARAMS"
    exit 1
fi
if [ ! -f "$REPO_DIR/$FACTS" ]; then
    err "facts file not found: $REPO_DIR/$FACTS"
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_DIR")"

# Run the editor inside the container with: repo, base model, EasyEdit,
# and the output dir mounted in. PYTHONPATH is set so the lamark package
# under learning/src/ is importable without `pip install -e`.
CONTAINER_NAME="lamark-edit-$$"
DRY_FLAG=""
[ "$DRY_RUN" -eq 1 ] && DRY_FLAG="--dry-run"
ONLY_FLAG=""
[ -n "$ONLY" ] && ONLY_FLAG="--only $ONLY"

log "starting container $CONTAINER_NAME ..."
docker run --rm \
    --name "$CONTAINER_NAME" \
    --gpus all \
    --shm-size 16g \
    -v "$REPO_DIR:/workspace/lamark" \
    -v "$MODEL_DIR:/model" \
    -v "$OUTPUT_DIR:/output" \
    -v "$EASYEDIT_ROOT:/easyedit" \
    -e PYTHONPATH=/workspace/lamark/src \
    -w /workspace/lamark \
    "$TRAIN_IMAGE" \
    bash -c "
        set -euo pipefail
        pip install --quiet pyyaml httpx 2>&1 | tail -2 || true
        python -m lamark.knowledge_edit.edit_runner \
            --base-model /model \
            --facts $FACTS \
            --hparams $HPARAMS \
            --output /output \
            --method $METHOD \
            --easyedit-root /easyedit \
            $ONLY_FLAG \
            $DRY_FLAG
    "

if [ "$DRY_RUN" -eq 1 ]; then
    ok "dry-run complete — no model written"
    exit 0
fi

# Install the Lamark chat template into the edited model dir too, so the
# edited model also defaults to the Lamark identity at serve time.
log "installing Lamark chat template into edited model dir ..."
"$REPO_DIR/scripts/install_chat_template.sh" "$OUTPUT_DIR"

ok "edited model ready: $OUTPUT_DIR"
echo
echo "Next steps:"
echo "  1. Stop the running vLLM (./serve_vllm.sh stop) if any"
echo "  2. MODEL_DIR=$OUTPUT_DIR ADAPTER_DIR='' ./serve_vllm.sh start"
echo "  3. Run the evaluator from another shell:"
echo "     python -m lamark.knowledge_edit.evaluator \\"
echo "       --facts $FACTS --model base --out /tmp/edit-report.json"
