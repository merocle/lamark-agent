#!/usr/bin/env bash
# Local orchestrator — apply L3 ROME/MEMIT edits to the Nemotron-30B base
# model on DGX Spark.
#
# Mirrors run_spark_training.sh in shape:
#   1. scp the repo's spark/ scripts + facts + hparams to the host
#   2. ssh in and run 05_run_edit.sh inside the training container
#   3. (optional) restart vLLM pointing at the edited model and run the
#      evaluator from this side; for v1 we leave that step manual
#
# Usage (from your local machine):
#   ./learning/scripts/run_spark_edit.sh
#   ./learning/scripts/run_spark_edit.sh --dry-run
#   ./learning/scripts/run_spark_edit.sh --only lamark-vs-lamarck,lamark-self-id
#   ./learning/scripts/run_spark_edit.sh --method rome
#   ./learning/scripts/run_spark_edit.sh --edit-tag lamark-facts-v2
#
# Requirements (local machine):
#   - ssh + scp access to $SPARK_HOST (OpenSSH; no rsync needed)
#
# Requirements (DGX Spark):
#   - Base model already downloaded at ~/.lamark/models/hf/<slug>/
#   - EasyEdit cloned at ~/EasyEdit (git clone https://github.com/zjunlp/EasyEdit)
#   - Docker with NVIDIA container toolkit
#   - nvcr.io/nvidia/pytorch:26.01-py3 (or override TRAIN_IMAGE)

set -euo pipefail

SPARK_HOST="${SPARK_HOST:-jetbrains@10.212.212.1}"
SPARK_REPO_DIR="${SPARK_REPO_DIR:-lamark-agent}"
SSH_KEY="${SSH_KEY:-}"
EDIT_TAG="${EDIT_TAG:-lamark-facts-v1}"
METHOD="memit"
ONLY=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)  DRY_RUN=1; shift ;;
        --method)   shift; METHOD="$1"; shift ;;
        --only)     shift; ONLY="$1"; shift ;;
        --edit-tag) shift; EDIT_TAG="$1"; shift ;;
        -h|--help)  sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

log()  { printf "\033[1;34m[spark-edit]\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[spark-edit]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[spark-edit]\033[0m %s\n" "$*" >&2; }

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
if [ -n "$SSH_KEY" ]; then
    SSH_OPTS+=(-i "$SSH_KEY")
fi

LOCAL_LEARNING_DIR="$(dirname "$0")/.."
SCRIPT_DIR="$LOCAL_LEARNING_DIR/scripts"

log "Checking SSH connectivity to $SPARK_HOST ..."
if ! ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "echo ok" &>/dev/null; then
    err "Cannot reach $SPARK_HOST. Check SPARK_HOST and your SSH key."
    exit 2
fi
ok "SSH connection OK."
echo

log "Syncing repo bits to $SPARK_HOST:$SPARK_REPO_DIR/ ..."
ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "
    mkdir -p $SPARK_REPO_DIR/learning/scripts/spark \
             $SPARK_REPO_DIR/learning/configs/memit \
             $SPARK_REPO_DIR/learning/data \
             $SPARK_REPO_DIR/learning/src/lamark/knowledge_edit \
             $SPARK_REPO_DIR/learning/templates \
             $SPARK_REPO_DIR/learning/scripts
"
scp "${SSH_OPTS[@]}" -r "$SCRIPT_DIR/spark/." \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/spark/"
scp "${SSH_OPTS[@]}" "$SCRIPT_DIR/install_chat_template.sh" \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/scripts/"
scp "${SSH_OPTS[@]}" "$LOCAL_LEARNING_DIR/configs/memit/nemotron-h-30b-a3b.yaml" \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/configs/memit/"
scp "${SSH_OPTS[@]}" "$LOCAL_LEARNING_DIR/data/lamark_facts.jsonl" \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/data/"
scp "${SSH_OPTS[@]}" "$LOCAL_LEARNING_DIR/templates/lamark_chat_template.jinja" \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/templates/"
scp "${SSH_OPTS[@]}" -r "$LOCAL_LEARNING_DIR/src/lamark/knowledge_edit/." \
    "$SPARK_HOST:$SPARK_REPO_DIR/learning/src/lamark/knowledge_edit/"
ok "Repo bits synced."
echo

DRY_FLAG=""
[ "$DRY_RUN" -eq 1 ] && DRY_FLAG="--dry-run"

log "Running L3 editor on Spark (method=$METHOD, tag=$EDIT_TAG, only='$ONLY') ..."
ssh "${SSH_OPTS[@]}" "$SPARK_HOST" "
    chmod +x $SPARK_REPO_DIR/learning/scripts/spark/05_run_edit.sh
    chmod +x $SPARK_REPO_DIR/learning/scripts/install_chat_template.sh
    cd $SPARK_REPO_DIR
    METHOD='$METHOD' \
    EDIT_TAG='$EDIT_TAG' \
    ONLY='$ONLY' \
    learning/scripts/spark/05_run_edit.sh $DRY_FLAG
"
echo

if [ "$DRY_RUN" -eq 1 ]; then
    ok "=== Dry run complete ==="
    exit 0
fi

ok "=== L3 edit pipeline complete ==="
echo
echo "Edited model on Spark:"
echo "  ~/.lamark/models/edited/nvidia_NVIDIA-Nemotron-3-Nano-30B-A3B-BF16__${EDIT_TAG}/"
echo
echo "To serve and evaluate (run on Spark):"
echo "  ./serve_vllm.sh stop"
echo "  MODEL_DIR=\$HOME/.lamark/models/edited/.../${EDIT_TAG}/ \\"
echo "    ADAPTER_DIR='' ./learning/scripts/spark/serve_vllm.sh start"
echo "  python -m lamark.knowledge_edit.evaluator --facts learning/data/lamark_facts.jsonl --model base"
