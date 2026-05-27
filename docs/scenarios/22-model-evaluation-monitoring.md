# 22 — Model Evaluation & Monitoring

> **Phase:** P7–P8 (trainer + eval gate) + P6 (gateway, ACP connectors).
> **One-liner:** Lamark watches the nightly trainer output, reads evaluation
> metrics and forgetting-probe results, posts a concise Slack digest to the
> ML team, and triggers a rollback if a regression exceeds a configured
> threshold — all while remembering which model checkpoints performed best
> for which task domains.

---

## North-star contribution

- **Domain quality (ML ops).** Turns raw trainer logs into an actionable
  monitoring loop: nightly trainer run → model checkpoint → eval suite →
  forgetting-probe → signal → (1) KB‑recorded metric fact, (2) Slack
  summary, (3) optional auto‑rollback. Eliminates the need for separate
  Grafana dashboards and manual post‑mortems.
- **Agent-side self‑improvement.** Emits:
  - `memory_fact` writes: checkpoint → benchmark score mapping (e.g.,
    “Qwen3‑35B‑A3B‑v2 → 87.2 % MMLU”). Recalled by future evaluation
    agents to bias sampling toward high‑performing configurations.
  - `skill_draft` candidates: reusable evaluation‑template prompts (e.g.,
    “Compare two LoRA adapters on MMLU‑math”) promoted by Curator after ≥ 3
    uses.
  - `reinforce_signal=success` when a checkpoint passes all eval gates;
    `fail` when a regression triggers a rollback.
- **Model-side self‑improvement.** Trace bundles for evaluation runs are
  consumed by the trainer as **Nemotron‑Agentic‑v2** `evaluation.jsonl`
  entries; DPO pairs arise when a rejected checkpoint is swapped out for a
  promoted one.

---

## Idea

The ML platform ships nightly LoRA adapters for each base model. Engineers
need a fast way to know which adapters are still “green” and which have
regressed. Lamark runs automatically after each trainer cycle, reads the
`eval_sets` table in the knowledge‑base, compares the latest checkpoint
against baseline scores, and posts a Slack message to `#ml‑monitoring`:

```
📊 Model eval – 2026‑05‑25
✅ Qwen3‑35B‑A3B‑v2   87.2 % MMLU  (↑0.3)
⚠️ Gemma4‑27B‑v2      84.1 % MMLU  (↓1.4)  ↳ *rollback triggered*
✅ Nemotron‑Nano‑30B  79.5 % MMLU  (steady)
```

The message includes a link to the full evaluation bundle in knowledge‑base.
If a regression crosses the configured threshold, Lamark automatically
creates a `rollback` task on the Kanban board (`plan/16 §"Forgetting
probe → auto‑rollback"`). The rollback agent pulls the previous checkpoint
from the trainer’s artifact store and updates the model registry. Engineers
can later query “what checkpoint was used for production on 2026‑05‑20?”
and retrieve the exact trace bundle for audit.

---

## Actors

| Actor | Role |
|---|---|
| **ML Trainer** | Python process that produces LoRA adapters and writes `trace.jsonl` bundles to `~/.lamark/traces/<run_id>/`. |
| **Evaluation coordinator** | `lamark-coordinator` sub‑agent. Schedules the eval run (triggered by trainer completion), gathers results, runs the forgetting‑probe gate. |
| **Eval suite** | Tool that loads a checkpoint, runs MMLU, GSM8K, HumanEval, etc., and returns a structured JSON scorecard. |
| **Forgetting‑probe agent** | Checks each test set against a gold‑set stored in KB; emits `reinforce_signal=success` only if all probe scores meet a pre‑defined threshold. |
| **Model registry** | External service that stores canonical checkpoint IDs. Updated by Lamark rollback agent. |
| **Knowledge‑base** | Stores per‑checkpoint metric facts and the full evaluation trace. |
| **Gateway** | Slack adapter — posts the digest and rollback alerts to the ML team channel (`plan/09 §"Gateway"`). |
| **ACL/Permissions** | `lamark-policy` ensures only the `eval-suite` tool is allowed to run destructive probes. |

---

## Trigger

1. **Nightly pulse (cron)**  
   ```
   $ lamark agent run --profile eval-pulse --schedule "0 2 * * *"
   ```
   Runs at 02:00 local time after the trainer’s nightly window.

2. **Manual trigger (debug)**  
   ```
   $ lamark eval run --checkpoint Qwen3-35B-A3B-v2 --suite full
   ```

Both routes invoke the evaluation coordinator.

---

## Pipeline

### Step 0 — Bootstrap & context load

1. Eval coordinator reads `GET /memory/search?q=eval‑config` — retrieves the
   configured evaluation suite (list of benchmark IDs, thresholds, slack
   channel, rollback trigger level).  
2. Loads the **baseline checkpoint** reference from KB (`GET
   /knowledge/models/baseline/<model-id>`).  
3. Posts a `SessionStart` event to the gateway so the team knows an eval run
   is about to begin.

### Step 1 — Checkpoint download

4. Calls `ModelTool::PullCheckpoint(<checkpoint-id>)` — fetches the LoRA
   adapter from the trainer’s artifact store (S3, GCS, or local cache).  
5. Writes the checkpoint to the sandbox runtime (`plan/05c §"sandbox"`).

### Step 2 — Run evaluation suite

6. Executes `EvalSuiteTool::Run(suite_name, checkpoint_path)` — spawns the
   eval process, streams metric deltas, and writes a structured
   `evaluation.jsonl` bundle (Nemotron‑Agentic‑v2 format).  
7. Each benchmark result is also posted as a `memory_fact`:
   `{checkpoint, benchmark, score, timestamp}`.

### Step 3 — Forgetting‑probe gate

8. Evaluator reads the baseline scores from KB (`GET /memory/search?q=baseline‑scores`).  
9. For each benchmark, compares the new score against the baseline;
   if **Δ ≤ ‑threshold**, tags the checkpoint as `regressed`.  
10. If *any* benchmark is regressed beyond the configured rollback threshold,
    the agent posts a `RollbackTask` on the Kanban board (`plan/16 §"Forgetting
    probe → auto‑rollback"`).  

### Step 4 — Signal generation

11. If **no regression**, `reinforce_signal=success` is attached to the
    evaluation bundle and stored in KB.  
12. If **regression**, `reinforce_signal=fail` is attached and the
    rollback task is created.

### Step 5 — Knowledge‑base write & Slack digest

13. KB client writes:
    - `POST /knowledge/eval_sets` with the full `evaluation.jsonl` bundle.  
    - `POST /memory/facts` for each checkpoint‑score mapping.  
14. Slack digest is built from the gathered facts:
    - List of checkpoints with scores (highlight regressed ones).  
    - Link to the evaluation trace in KB.  
    - Optional “rollback needed?” flag with a clickable link to the rollback
      task.  
15. Gateway posts the digest to `#ml‑monitoring`.

### Step 6 — Rollback (if needed)

16. Rollback agent polls the Kanban board; when it sees a `RollbackTask`:
    - Calls `ModelTool::PromoteCheckpoint(<previous_stable_id>)`  
    - Updates the model registry to point to the promoted checkpoint.  
    - Posts a follow‑up Slack message: “↩️ Rolled back to
      Qwen3‑35B‑A3B‑v1 (87.9 % MMLU)”.  
    - Marks the rollback task as `completed`.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0–1 (bootstrap, config) | `lamark-coordinator`, `lamark-kb-client`, `lamark-config` | 05a, 07a |
| 2 (checkpoint pull) | `lamark-tools` (ModelTool), `lamark-providers` | 05, 04 |
| 3 (evaluation) | `lamark-eval` (custom tool), `lamark-core` (turn loop) | custom |
| 4 (probe) | `lamark-policy` (allowlist), `lamark-hooks` | 06 |
| 5 (signal) | `lamark-trace`, `lamark-kb-client` | 06, 07a |
| 6 (Slack digest) | `lamark-gateway` (Slack adapter) | 09 |
| 7 (rollback) | `lamark-coordinator`, `ModelTool` | 16 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Eval suite crashes** | Eval bundle is marked `aborted`; trace is queued in outbox; retry scheduled after 5 min. |
| **Network error pulling checkpoint** | Agent falls back to cached checkpoint (if present); otherwise posts an error Slack digest and aborts. |
| **Threshold mis‑configured** (too low) | Agent logs a warning, escalates to a human Slack alert (“⚠️ Evaluation threshold unusually low – review config”). |
| **Rollback agent fails to promote** | Retries up to 3 times with exponential backoff; if still failing, posts a “manual rollback required” alert and leaves the task in `in_progress`. |
| **Slack message fails to send** | Retries with jitter; if still failing, writes the digest to a local file and posts a warning to the console. |
| **Memory write to KB fails** | Writes to local outbox; retries every 2 min until success; trace bundle remains available for later upload. |

---

## Acceptance criteria

- [ ] Nightly eval pulse runs automatically and produces a Slack digest within 2 min of completion.  
- [ ] Regressed checkpoint is automatically added to a rollback task and promoted when the task completes.  
- [ ] All evaluation scores are stored as `memory_fact` entries for future recall.  
- [ ] When a checkpoint passes all thresholds, `reinforce_signal=success` is recorded in the bundle.  
- [ ] A manual `lamark eval run --checkpoint X --suite light` can be invoked and produces the same Slack digest format.  
- [ ] Missing KB write is retried automatically and does not block the eval pipeline.  
- [ ] Rollback agent correctly restores the previous checkpoint in the model registry.  

---

## Self‑improvement assertions

1. **SFT samples for evaluation.** Each evaluation run produces ≥ 1 Nemotron‑Agentic‑v2 entry covering `EvalSuiteTool → Run → memory_fact`.  
2. **DPO pairs from rollbacks.** A checkpoint that is rejected and later promoted yields a `(rejected, promoted)` pair the trainer can consume.  
3. **Threshold tuning improves recall.** After ≥ 3 runs, the agent learns (via Curator‑suggested config updates) a more optimal rollback threshold, reducing false‑positive rollbacks by ≥ 15 %.  
4. **Memory recall speeds up future evals.** When re‑evaluating a checkpoint, the agent skips fetching baseline scores (they’re recalled from memory), shaving ≥ 30 s off pipeline latency.  
5. **Skill promotion reduces manual config.** Curator promotes an “Evaluation Suite Template” skill after ≥ 3 successful runs; subsequent evaluations auto‑load the template, eliminating manual config drift.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Nightly eval pulse scheduling | plan/15 §"Nightly training" + custom schedule | _audit_ |
| Checkpoint pull & promotion | plan/05c §"sandbox" + plan/16 | _audit_ |
| Evaluation suite execution | custom tool (add to plan/16) | _audit_ |
| Forgetting‑probe gate | plan/16 §"Forgetting probe → auto‑rollback" | _audit_ |
| Slack digest via gateway | plan/09 §"Gateway" | _audit_ |
| KB fact writes (scores, checkpoint mapping) | plan/07a | _audit_ |
| Slack alert on rollback | plan/09 §"Gateway" | _audit_ |
| Agent‑side signal (`reinforce_signal`) | plan/07a, plan/08 | _audit_ |
| Failure‑mode handling (retry, outbox) | plan/11 §"build-test-deploy" | _audit_ |

---

## Open questions

1. **Eval suite containerisation.** Should the suite run in a sandbox container or on the host? Sandbox isolates dependencies but adds latency.  
2. **Score‑type granularity.** Do we store raw metric deltas (e.g., token‑level accuracy) or just aggregated percentages? Storing raw values enables richer future analysis but inflates KB size.  
3. **Rollback automation policy.** Should Lamark auto‑rollback on any regression, or only after a configurable number of consecutive failures? This needs an ADR.  
4. **Checkpoint provenance.** How are checkpoint IDs signed or otherwise proven to come from the trainer’s authorized output? Prevents spoofed rollbacks.  
5. **Evaluation cost accounting.** Each suite call consumes compute credits; should the agent track and report cost per run? Integration with internal cost‑tracking service needed.  
