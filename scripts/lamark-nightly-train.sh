#!/bin/bash
# Lamark nightly retrain.
#
# Pipeline:
#   1. Build curation plan from the archive (only pairs added since last run).
#   2. If empty → nothing to learn → exit 0.
#   3. Train a fresh LoRA adapter inside the lamark/vllm container.
#   4. Hot-load the new adapter into the running vLLM via /v1/load_lora_adapter.
#   5. Run the eval-gate against the new adapter.
#   6. If gate passes → swap default model alias to point at the new adapter.
#      If gate fails → unload the new adapter, keep the previous one active.
#
# All output goes to ~/.lamark/logs/nightly-train-<date>.log.
# Designed to be invoked from a systemd timer or crontab line:
#   0 3 * * *  /home/jetbrains/lamark-agent/scripts/lamark-nightly-train.sh
set -euo pipefail

LAMARK_HOME="${LAMARK_HOME:-$HOME/.lamark}"
REPO="${LAMARK_REPO:-$HOME/lamark-agent}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://127.0.0.1:8000/v1}"
ADAPTER_DIR="${LAMARK_ADAPTER_DIR:-$LAMARK_HOME/adapters}"

# Resolve base model path from the registry rather than hardcoding the
# BF16 entry — Lamark may switch between BF16 and FP8 (Stage 2 of the
# perf push), and the trainer must follow the registry-declared default.
# Override with LAMARK_BASE_MODEL=/abs/path for ad-hoc runs against a
# non-default model.
export HERMES_HOME_PRE="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
export LAMARK_HOME
if [ -n "${LAMARK_BASE_MODEL:-}" ]; then
    BASE_MODEL_DIR="$LAMARK_BASE_MODEL"
else
    # Resolve the training base via the registry. The serving stack may
    # be running a quantized model (FP8 / INT4) but HF Transformers
    # refuses to fine-tune those — see the ValueError on
    # QuantizationMethod.FP8. Each entry can declare `train_with:` to
    # point at a sibling registry entry whose hf_id we use instead.
    # `model.default` in config.yaml may be the registry slug or a
    # served-model alias; on KeyError, fall back to the tier-S default.
    BASE_MODEL_DIR=$(PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" - <<'PY'
import os, yaml
from lamark.registry import load_registry, get_model, ModelEntry

lamark_home = os.environ["LAMARK_HOME"]
hermes_home = os.environ["HERMES_HOME_PRE"]
cfg = yaml.safe_load(open(f"{hermes_home}/config.yaml").read()) or {}
name = (cfg.get("model") or {}).get("default") or ""

entry = None
try:
    entry = get_model(name)
except Exception:
    for n, e in load_registry().items():
        if isinstance(e, ModelEntry) and e.tier == "S" and e.default_for_tier:
            entry = e
            break

# If the resolved entry declares a sibling training base (e.g. FP8 →
# BF16), prefer that. Otherwise train against the entry's own weights.
if entry is not None and entry.serving.train_with:
    try:
        train_entry = get_model(entry.serving.train_with)
        entry = train_entry
    except Exception:
        pass  # fall through to entry itself

if entry is not None:
    flat = entry.hf_id.replace("/", "_")
    print(f"{lamark_home}/models/hf/{flat}")
PY
)
    BASE_MODEL_DIR="${BASE_MODEL_DIR:-$LAMARK_HOME/models/hf/Qwen_Qwen3.6-35B-A3B}"
fi

LOG_DIR="$LAMARK_HOME/logs"
mkdir -p "$LOG_DIR" "$ADAPTER_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/nightly-train-$TS.log"

# Run-state (read by the EXIT trap below). The trap is the single place
# that rolls back / notifies on any NON-promotion exit (gate reject, crash,
# signal, set -e), so the candidate adapter can never be left live after a
# failed or interrupted run.
PREV_TARGET=""        # adapters/current target captured BEFORE we touch it
CANDIDATE_LINKED=0    # 1 once current → candidate (so rollback is needed)
PROMOTED=0            # 1 once the gate passed and promotion completed
GATE_RAN=0            # 1 once the eval-gate actually executed
FAIL_REASON=""        # set by fail() so the trap can report the cause
NEW_PAIRS=0           # genuinely-new (unconsumed) pairs — run-threshold input
N_PAIRS=0             # cumulative training-set size (pairs actually trained on)
ADAPTER_NAME=""

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
# fail() no longer notifies directly — it records the reason and exits; the
# EXIT trap performs rollback + the single user notification.
fail() { FAIL_REASON="$1"; log "FAIL: $*"; exit 1; }

# Send a Telegram message to the bot owner. Uses the same bot token the
# gateway polls with, plus TELEGRAM_HOME_CHANNEL (the owner's chat_id —
# already populated by the gateway setup wizard). Pure HTTPS POST, no
# Hermes import required, so the trainer doesn't have to share venv
# state with the running daemon.
#
# All notification failures are SILENT — we never let a failed Telegram
# call break the training pipeline.
notify_user() {
    # Sends one Telegram message in HTML parse mode (more forgiving than
    # Markdown for content with underscores in identifiers / paths).
    local body="$1"
    local token chat_id env_file="${HERMES_HOME:-$LAMARK_HOME/hermes-home}/.env"
    [ -f "$env_file" ] || return 0
    token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    chat_id=$(grep -E '^TELEGRAM_HOME_CHANNEL=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
    [ -n "$token" ] && [ -n "$chat_id" ] || return 0
    curl -sS --max-time 10 "https://api.telegram.org/bot$token/sendMessage" \
        -d "chat_id=$chat_id" \
        -d "parse_mode=HTML" \
        --data-urlencode "text=$body" \
        >> "$LOG" 2>&1 || true
}

notify_success() {
    local adapter_name="$1"
    notify_user "🎓 <b>Lamark training complete</b>
• New pairs since last train: ${NEW_PAIRS}
• Trained on (cumulative): ${N_PAIRS}
• Adapter: <code>${adapter_name}</code>
• Eval gate: ✓ promoted — now serving as <code>lamark</code>"
}

notify_rejection() {
    local adapter_name="$1" probe_summary="${2:-}"
    notify_user "⚠️ <b>Lamark training: adapter rejected</b>
• New pairs since last train: ${NEW_PAIRS}
• Adapter: <code>${adapter_name}</code>
• Eval gate failed${probe_summary:+ — }${probe_summary}
• Previous adapter rolled back and still serving"
}

# Distinct from a rejection: the previous adapter could NOT be brought back
# online after rollback. The user must intervene — do not pretend the agent
# is fine.
notify_offline() {
    local adapter_name="$1"
    notify_user "🛑 <b>Lamark AGENT OFFLINE — manual restart needed</b>
• A training run failed and the previous adapter did not come back online.
• Run <code>lamark serve start</code> on the host to recover.
• Adapter under test: <code>${adapter_name}</code>
• Log: <code>$LOG</code>"
}

notify_skip() {
    local n_pairs="$1" threshold="$2"
    notify_user "ℹ️ <b>Lamark training skipped</b>
• Pairs accumulated: ${n_pairs} / ${threshold} threshold
• Will retry on next scheduled run"
}

notify_failure() {
    local reason="$1"
    notify_user "❌ <b>Lamark training failed</b>
• Reason: <code>${reason}</code>
• Check log: <code>$LOG</code>"
}

# Single readiness probe: a real (tiny) chat completion against the base
# model name, which every serve config exposes. Returns 0 when the engine
# can actually serve (not just /v1/models reachable).
ready_once() {
    curl -fsS -m 5 -H "Content-Type: application/json" \
        -d '{"model":"qwen-base","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' \
        "$VLLM_BASE_URL/chat/completions" >/dev/null 2>&1
}

# Poll ready_once for up to ~20 min (model load + CUDA graph + LoRA attach).
wait_ready() {
    local i
    for i in $(seq 1 40); do  # 40 × 30s = 20 min cap
        if ready_once; then
            log "vLLM ready (chat completion succeeded) after $((i*30))s"
            return 0
        fi
        sleep 30
    done
    return 1
}

write_history() {
    # One line per terminal state. Best-effort (history is advisory) but we
    # try fsync via Python. Carries new vs cumulative counts + gate verdict.
    local action="$1" probe_detail="${2:-}"
    LAMARK_HISTORY_ACTION="$action" \
    LAMARK_HISTORY_TS="$TS" \
    LAMARK_HISTORY_NEW="$NEW_PAIRS" \
    LAMARK_HISTORY_CUM="$N_PAIRS" \
    LAMARK_HISTORY_ADAPTER="${ADAPTER_NAME:-}" \
    LAMARK_HISTORY_PROBES="$probe_detail" \
    "$LAMARK_HOME/venv/bin/python" - <<'PY' >> "$LOG" 2>&1 || true
import json, os
rec = {
    "ts": os.environ.get("LAMARK_HISTORY_TS", ""),
    "action": os.environ.get("LAMARK_HISTORY_ACTION", ""),
    "new_pairs": int(os.environ.get("LAMARK_HISTORY_NEW") or 0),
    "cumulative_pairs": int(os.environ.get("LAMARK_HISTORY_CUM") or 0),
    "adapter_name": os.environ.get("LAMARK_HISTORY_ADAPTER", ""),
}
probes = os.environ.get("LAMARK_HISTORY_PROBES", "")
if probes:
    rec["gate_probes"] = probes
home = os.environ["LAMARK_HOME"]
with open(f"{home}/train-history.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    f.flush(); os.fsync(f.fileno())
print(f"history written: {rec}")
PY
}

# Roll back adapters/current to the previously-promoted adapter and bring
# serving back up. Used by the EXIT trap on any non-promotion exit.
restore_previous() {
    if [ "$CANDIDATE_LINKED" = "1" ]; then
        if [ -n "$PREV_TARGET" ] && [ -e "$PREV_TARGET" ]; then
            ln -sfn "$PREV_TARGET" "$ADAPTER_DIR/current"
            log "Rolled back: adapters/current → $PREV_TARGET"
        else
            # No usable previous adapter (first-ever run, or it was cleaned
            # up). Clear the symlink so serve.sh serves base only rather than
            # chasing a dangling link (which would silently drop identity).
            rm -f "$ADAPTER_DIR/current"
            log "No previous adapter to roll back to — current cleared (base only)"
        fi
    fi
    "$REPO/scripts/cmd/serve.sh" restart >> "$LOG" 2>&1 || true
}

# EXIT trap: the ONLY non-promotion handler. Fires on gate-reject (clean
# exit 0), crash, signal, or set -e. Idempotent w.r.t. PROMOTED.
on_exit() {
    local rc=$?
    trap - EXIT
    if [ "$PROMOTED" = "1" ]; then
        exit "$rc"
    fi
    # Non-promotion: roll back to the previous adapter and verify online.
    log "Non-promotion exit (rc=$rc) — restoring previous adapter"
    restore_previous

    # Drop the candidate dir — dead weight whether it was rejected or the
    # run crashed mid-way.
    if [ -n "${ADAPTER_NAME:-}" ] && [ -d "$ADAPTER_DIR/$ADAPTER_NAME" ]; then
        docker run --rm -v "$ADAPTER_DIR:/work" alpine \
            rm -rf "/work/$ADAPTER_NAME" >> "$LOG" 2>&1 || true
        log "Removed candidate adapter dir $ADAPTER_NAME"
    fi

    if wait_ready; then
        if [ "$GATE_RAN" = "1" ] && [ -z "$FAIL_REASON" ]; then
            notify_rejection "${ADAPTER_NAME:-?}" "${GATE_PROBE_SUMMARY:-}"
            write_history "rejected" "${GATE_PROBE_SUMMARY:-}"
        else
            notify_failure "${FAIL_REASON:-unexpected exit (rc=$rc)}"
            write_history "failed"
        fi
    else
        # Could not bring the previous adapter back — loud, distinct alert.
        notify_offline "${ADAPTER_NAME:-?}"
        write_history "offline"
    fi
    exit "$rc"
}
# NOTE: the trap is armed later, right before the first mutation (the
# current→candidate symlink swap). Early clean exits below (empty plan,
# below-threshold skip) happen before any state is touched and must NOT
# trigger rollback/notify.

log "=== Lamark nightly retrain starting ==="

# --- 0. Read training config from $HERMES_HOME/config.yaml -----------------
# Hybrid trigger: timer fires per `training.frequency` (set by setup wizard),
# but the actual retrain runs only when `training.min_pairs` are accumulated
# since the last successful retrain. Lets the user say "check daily, but
# don't waste GPU if I added 3 messages."
#
# Override via env vars LAMARK_TRAIN_MIN_PAIRS / LAMARK_TRAIN_FORCE for ad-hoc
# manual invocations (`lamark train --now --force`).
HERMES_HOME="${HERMES_HOME:-$LAMARK_HOME/hermes-home}"
TRAIN_MIN_PAIRS="${LAMARK_TRAIN_MIN_PAIRS:-}"
TRAIN_FORCE="${LAMARK_TRAIN_FORCE:-0}"

if [ -z "$TRAIN_MIN_PAIRS" ] && [ -f "$HERMES_HOME/config.yaml" ]; then
    TRAIN_MIN_PAIRS=$(PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" -c "
import yaml
try:
    cfg = yaml.safe_load(open(r'$HERMES_HOME/config.yaml').read()) or {}
    t = (cfg.get('training') or {})
    print(int(t.get('min_pairs', 50)))
except Exception:
    print(50)
")
fi
TRAIN_MIN_PAIRS="${TRAIN_MIN_PAIRS:-50}"
log "Threshold: min_pairs=${TRAIN_MIN_PAIRS}, force=${TRAIN_FORCE}"

# --- 1. Build plan ---------------------------------------------------------
# The training set is CUMULATIVE (the dispatcher trains from base each
# night, so excluding already-trained pairs would forget prior nights).
# Alongside the plan we write an ID manifest: the meta.id of every record
# trained on, so a successful promote can mark exactly those pairs consumed.
PLAN="$LAMARK_HOME/train-plan-nightly.jsonl"
IDS_MANIFEST="$LAMARK_HOME/train-plan-nightly.ids.json"
FALLBACK_MARKER="$LAMARK_HOME/train-plan-nightly.fallback"
rm -f "$FALLBACK_MARKER"
log "Building plan -> $PLAN"
PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" - <<PY >> "$LOG" 2>&1
import json
from pathlib import Path
plan_path = Path("$PLAN")
ids_path = Path("$IDS_MANIFEST")
try:
    from lamark.train.curation import build_nightly_plan
    plan = build_nightly_plan()
    ids = []
    with plan_path.open("w", encoding="utf-8") as f:
        for rec in plan.records:
            f.write(json.dumps({"messages": rec["messages"]}, ensure_ascii=False) + "\n")
            rid = (rec.get("meta") or {}).get("id")
            if rid:
                ids.append(rid)
    ids_path.write_text(json.dumps(ids), encoding="utf-8")
    print(f"plan has {len(plan.records)} records, {len(ids)} with consumable ids")
except Exception as e:
    # Curation pipeline not reachable: fall back to dense identity + session
    # seeds. These have NO archive ids, so they cannot be marked consumed —
    # leave an empty manifest and a fallback marker so the promote step skips
    # consumption (and says so) rather than silently no-op'ing.
    print(f"curation fallback: {e}")
    from lamark.bootstrap.seed_identity_dense import _build_pairs as dense
    from lamark.bootstrap.seed_session import _build_pairs as sess
    pairs = dense() + sess()
    with plan_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            rec = {"messages": [
                {"role": "user", "content": p.question},
                {"role": "assistant", "content": p.answer},
            ]}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ids_path.write_text("[]", encoding="utf-8")
    Path("$FALLBACK_MARKER").write_text("1", encoding="utf-8")
    print(f"fallback plan: {len(pairs)} pairs (no consumable ids)")
PY

if [ ! -s "$PLAN" ]; then
    log "Plan empty — nothing to train on. Exiting."
    exit 0   # before the trap is armed — clean no-op
fi
N_PAIRS=$(wc -l < "$PLAN" | tr -d ' ')
log "Plan: $N_PAIRS pairs (cumulative training set)"

# --- 1b. Threshold check ---------------------------------------------------
# The run threshold is on NEW (unconsumed) pairs, not the cumulative plan
# size — otherwise once seeded the count is always above threshold and the
# trainer churns nightly on a near-identical set.
NEW_PAIRS=$(PYTHONPATH="$REPO/src" "$LAMARK_HOME/venv/bin/python" - <<'PY' 2>>"$LOG"
import os
try:
    from lamark.archive import Archive
    from lamark.train.curation import count_new_pairs
    root = os.path.join(os.environ["LAMARK_HOME"], "archive")
    print(count_new_pairs(Archive.open(root)))
except Exception:
    print(-1)
PY
)
NEW_PAIRS="${NEW_PAIRS:--1}"
if [ "$NEW_PAIRS" = "-1" ]; then
    log "WARN: could not compute new-pair count — using cumulative ($N_PAIRS)"
    NEW_PAIRS="$N_PAIRS"
fi
log "New (unconsumed) pairs: $NEW_PAIRS (threshold $TRAIN_MIN_PAIRS)"

if [ "$TRAIN_FORCE" != "1" ] && [ "$NEW_PAIRS" -lt "$TRAIN_MIN_PAIRS" ]; then
    log "SKIP: only $NEW_PAIRS new pairs, below threshold $TRAIN_MIN_PAIRS."
    log "Override with --force or lower training.min_pairs in config.yaml."
    write_history "skipped"
    # Notify when new content IS accumulating (meaningful: "12/50 so far") or
    # when forced. Stay silent on zero-new days so cron isn't spammy.
    if [ "$TRAIN_FORCE" = "1" ] || [ "$NEW_PAIRS" -gt 0 ]; then
        notify_skip "$NEW_PAIRS" "$TRAIN_MIN_PAIRS"
    fi
    exit 0   # before the trap is armed — clean no-op
fi

# --- 2. Train --------------------------------------------------------------
ADAPTER_NAME="nightly-$TS"
log "Training adapter $ADAPTER_NAME (this takes 2-10 minutes)..."

# Capture the currently-promoted adapter BEFORE we touch anything, and arm
# the rollback trap. From here on, any non-promotion exit (training crash,
# gate reject, signal, set -e) restores this target and brings serving back
# online — the candidate can never be left live.
PREV_TARGET="$(readlink -f "$ADAPTER_DIR/current" 2>/dev/null || true)"
log "Previous promoted adapter: ${PREV_TARGET:-<none>}"
trap on_exit EXIT

# The dispatcher runs INSIDE the container with $LAMARK_HOME mounted at
# /workspace/.lamark. Translate the host-side BASE_MODEL_DIR (under
# $LAMARK_HOME on the host) to its corresponding container path. We
# always derived BASE_MODEL_DIR under $LAMARK_HOME above, so this
# substitution covers every supported entry from the registry.
CONTAINER_BASE_MODEL="${BASE_MODEL_DIR/#$LAMARK_HOME/\/workspace\/.lamark}"
log "Base model (host): $BASE_MODEL_DIR"
log "Base model (container): $CONTAINER_BASE_MODEL"

# Free the GPU so the training container can fit. We stop the production
# serve container (lamark-vllm — see scripts/cmd/serve.sh) and remember to
# restart it via `lamark serve start` once the adapter is saved. Note:
# this DOES interrupt active chats for the training duration; an opt-in
# zero-downtime path via vLLM /v1/load_lora_adapter would be cleaner but
# is deferred until DFlash+LoRA combo stability lands upstream.
PRIOR_VLLM_RUNNING=0
if docker ps --filter "name=lamark-vllm" -q | grep -q .; then
    PRIOR_VLLM_RUNNING=1
    log "Stopping production vLLM (lamark-vllm) to free GPU for training..."
    docker stop lamark-vllm >> "$LOG" 2>&1 || true
    docker rm   lamark-vllm >> "$LOG" 2>&1 || true
    sleep 5
fi

docker run --rm --name lamark-train-$TS \
  --gpus all --ipc=host \
  --ulimit memlock=-1 --ulimit stack=67108864 --shm-size=16g \
  -e LAMARK_HOME=/workspace/.lamark \
  -e LAMARK_MODEL_DIR=/workspace/models \
  -e TORCH_CUDA_ARCH_LIST=12.1 \
  -v "$REPO":/workspace/lamark-agent \
  -v "$LAMARK_HOME":/workspace/.lamark \
  -v "$LAMARK_HOME/models":/workspace/models \
  -v "$ADAPTER_DIR":/workspace/adapters \
  -w /workspace/lamark-agent \
  -e PYTHONPATH=/workspace/lamark-agent/src \
  lamark/vllm:25.10 \
  bash -c "pip uninstall -y flash-attn flash_attn 2>/dev/null; pip install --no-deps 'torchao>=0.16' 2>&1 | tail -1; python /workspace/lamark-agent/src/lamark/train/dispatcher_spark.py --pairs-jsonl /workspace/.lamark/train-plan-nightly.jsonl --base-model $CONTAINER_BASE_MODEL --adapter-name $ADAPTER_NAME --lora-rank 16 --num-epochs 2 --learning-rate 2e-4 --per-device-batch-size 1 --grad-accum-steps 4 --output-dir /workspace/adapters" \
  >> "$LOG" 2>&1 || fail "training container exited non-zero"

if [ ! -f "$ADAPTER_DIR/$ADAPTER_NAME/adapter_model.safetensors" ]; then
    fail "adapter not saved at $ADAPTER_DIR/$ADAPTER_NAME"
fi
log "Adapter saved: $ADAPTER_DIR/$ADAPTER_NAME"

# --- 3. Promote candidate, then serve it for the gate ----------------------
# serve.sh mounts ONLY adapters/current (a symlink) under the alias `lamark`.
# To gate the CANDIDATE we must point `current` at it and serve it — vLLM
# never serves a per-timestamp name, so probing `nightly-<TS>` always 404'd
# (the wiring bug that auto-rejected every adapter). The production server
# was already stopped for training, so serving the unproven candidate during
# the gate is safe (no live users). On reject the EXIT trap rolls back.
ln -sfn "$ADAPTER_DIR/$ADAPTER_NAME" "$ADAPTER_DIR/current"
CANDIDATE_LINKED=1
log "Symlink: adapters/current → $ADAPTER_NAME (candidate, under gate)"

log "Starting vLLM with the candidate (auto-loads adapters/current)..."
"$REPO/scripts/cmd/serve.sh" start >> "$LOG" 2>&1 || true

# Warm-up wait: model load + CUDA graph compile + LoRA attach takes several
# minutes; /v1/models is reachable before the engine can serve a completion.
log "Waiting for vLLM to serve a real completion (up to 20 min)..."
if ! wait_ready; then
    fail "vLLM did not become ready within 20 min — cannot gate the candidate"
fi

# --- 4. Eval-gate (against the served alias, time-bounded) -----------------
# Probe the alias `lamark` — that is what vLLM serves for adapters/current
# (= the candidate right now). An overall timeout guards against a wedged
# engine making the probes hang for many minutes.
GATE_OUT="$LAMARK_HOME/eval-gate-last.json"
GATE_RAN=1
GATE_PROBE_SUMMARY=""
log "Running eval-gate against served alias 'lamark' (candidate $ADAPTER_NAME)..."
if PYTHONPATH="$REPO/src" timeout 600 "$LAMARK_HOME/venv/bin/python" \
       -m lamark.train.eval_gate \
       --adapter-name "lamark" \
       --base-url "$VLLM_BASE_URL" > "$GATE_OUT" 2>> "$LOG"
then
    GATE_PASSED=1
else
    GATE_PASSED=0
fi

# Summarise which probes failed (for the notification + history), best-effort.
GATE_PROBE_SUMMARY=$(GATE_OUT="$GATE_OUT" "$LAMARK_HOME/venv/bin/python" - <<'PY' 2>/dev/null || true
import json, os
try:
    r = json.load(open(os.environ["GATE_OUT"]))
    failed = [p["name"] for p in r.get("probes", []) if not p.get("passed")]
    print("failed: " + ", ".join(failed) if failed else "all probes passed")
except Exception:
    print("")
PY
)

if [ "$GATE_PASSED" != "1" ]; then
    # Clean rejection — fall through to the EXIT trap, which rolls back to
    # PREV_TARGET, restarts serving, verifies online, and notifies.
    log "FAIL: gate rejected candidate ($GATE_PROBE_SUMMARY) — rolling back."
    exit 0
fi

log "PASS: gate accepted $ADAPTER_NAME — promoting."

# Record the prior promoted adapter as `previous` so cleanup never deletes
# the rollback target, and so a future failed run has a lineage to restore.
if [ -n "$PREV_TARGET" ] && [ -e "$PREV_TARGET" ]; then
    ln -sfn "$PREV_TARGET" "$ADAPTER_DIR/previous"
    log "Symlink: adapters/previous → $PREV_TARGET (rollback lineage)"
fi

# Mark exactly the trained record IDs consumed, so they stop counting as
# "new". A write failure is NOT swallowed — it is surfaced loudly, because
# an unrecorded promote would let the same pairs re-qualify forever. (The
# adapter stays promoted; this is a warning, not a rollback.)
if [ -f "$FALLBACK_MARKER" ]; then
    log "Fallback plan used — no archive ids to mark consumed (skipping)."
else
    if PYTHONPATH="$REPO/src" \
       LAMARK_IDS_MANIFEST="$IDS_MANIFEST" LAMARK_ADAPTER_NAME="$ADAPTER_NAME" \
       "$LAMARK_HOME/venv/bin/python" - <<'PY' >> "$LOG" 2>&1
import json, os
from lamark.archive import Archive
ids = json.loads(open(os.environ["LAMARK_IDS_MANIFEST"]).read() or "[]")
root = os.path.join(os.environ["LAMARK_HOME"], "archive")
Archive.open(root).mark_consumed(ids, adapter=os.environ["LAMARK_ADAPTER_NAME"])
print(f"marked {len(ids)} pairs consumed by {os.environ['LAMARK_ADAPTER_NAME']}")
PY
    then
        log "Consumed ledger updated for $ADAPTER_NAME"
    else
        log "WARN: mark_consumed failed — these pairs may re-train next run"
        notify_user "⚠️ <b>Lamark</b>: adapter <code>$ADAPTER_NAME</code> promoted, but the consumed-ledger write failed — the same pairs may be retrained next run. Check <code>$LOG</code>."
    fi
fi

# Clean up old promoted adapter dirs. Keep the 2 newest by mtime AND always
# keep whatever `current` and `previous` resolve to (the rollback lineage),
# so cleanup can never delete the live or fallback-target adapter.
LIVE_TARGET="$(readlink -f "$ADAPTER_DIR/current" 2>/dev/null || true)"
PREV_LINK_TARGET="$(readlink -f "$ADAPTER_DIR/previous" 2>/dev/null || true)"
DELETE=""
for d in $(ls -dt "$ADAPTER_DIR"/nightly-*/ 2>/dev/null | tail -n +3); do
    rp="$(readlink -f "$d" 2>/dev/null || true)"
    [ -n "$rp" ] && [ "$rp" = "$LIVE_TARGET" ] && continue
    [ -n "$rp" ] && [ "$rp" = "$PREV_LINK_TARGET" ] && continue
    DELETE="$DELETE $d"
done
if [ -n "${DELETE// /}" ]; then
    log "Cleaning up old adapter dirs:$DELETE"
    docker run --rm -v "$ADAPTER_DIR:/work" alpine \
        sh -c "rm -rf $(echo "$DELETE" | sed "s|$ADAPTER_DIR|/work|g")" >> "$LOG" 2>&1 || true
fi

# Point config.yaml at the stable alias instead of a timestamp name.
HERMES_HOME_CFG="$LAMARK_HOME/hermes-home/config.yaml"
if [ -f "$HERMES_HOME_CFG" ]; then
    sed -i "s|^  default:.*|  default: lamark|" "$HERMES_HOME_CFG" >> "$LOG" 2>&1 || true
    log "Default model alias set to 'lamark' (stable) in $HERMES_HOME_CFG"
fi

# The candidate is ALREADY serving as `lamark` (we started it for the gate),
# so there is nothing to restart — promotion takes effect immediately.
PROMOTED=1
notify_success "$ADAPTER_NAME"
write_history "promoted" "$GATE_PROBE_SUMMARY"
log "=== Lamark nightly retrain complete (promoted) ==="
