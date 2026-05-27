# 10 — Training pipeline (Python)

> The only Python in the project. Runs on the GPU box. Pulls traces from
> `~/.lamark/traces/` and `../knowledge-base`, produces LoRA adapters, gates
> them through evals, promotes or rolls back.

**Code location:** sibling project `~/lamark-trainer/` (not in this cargo workspace).
**Data location:** `~/.lamark/training/` (mirrors hermes operational layout).
**Reference:** `~/Downloads/setup-guide (2).md` (Parts 1–4) — operational truth; this file is the project-side spec.

## Why Python here, Rust elsewhere

Unsloth, Megatron-Bridge, TRL, DeepSpeed, transformers, datasets, peft — the training stack is Python-native. There is no useful Rust port. We use Python *only* here, and only behind a clean filesystem + HTTP boundary so the Rust runtime never imports it.

## Pipeline shape (high-level)

```
   ┌──────────────────────────────────────────────────────────────────┐
   │                          Nightly run (T+0..9h)                    │
   │                                                                   │
   │ T+0:00  collect                                                   │
   │   - lamark trace export --since=yesterday   →  /raw/lamark.jsonl  │
   │   - kb_pull memory.episodes since=yesterday →  /raw/kb.jsonl       │
   │   - git_mining --since=yesterday            →  /raw/git.jsonl     │
   │   - pr_ingest                              →  /raw/pr.jsonl       │
   │   - youtrack_ingest / jira_ingest          →  /raw/issue.jsonl    │
   │   - slack_ingest                           →  /raw/slack.jsonl    │
   │                                                                   │
   │ T+0:30  redact stage-1 (secrets)                                  │
   │   - gitleaks + trufflehog + detect-secrets                        │
   │   - any verified finding blocks the sample                        │
   │                                                                   │
   │ T+0:45  redact stage-2 (PII)                                      │
   │   - Presidio + spaCy en_core_web_lg + GLiNER                      │
   │   - type-preserving substitution, salted, consistent within doc   │
   │                                                                   │
   │ T+1:00  transform: source → Nemotron-Agentic-v1 JSONL             │
│   - lamark traces: reducer already ran; consume reduced/           │
│     conversation.jsonl directly (reasoning stripped per plan/06)   │
│   - compacted sessions split at CompactionMarker boundary          │
│   - git/PR/issue/slack sources: converters in transform/           │
   │                                                                   │
   │ T+1:30  curate (optional; gated on teacher budget)                │
   │   - OSS-Instruct seeds                                            │
   │   - two-judge consensus (Claude+GPT or GPT+Gemini, both ≥ 4/5)    │
   │   - execution-based filter for code (Docker sandbox)              │
   │                                                                   │
   │ T+2:30  quality + dedup + decontaminate                           │
   │   - PPL outlier (drop top + bottom 5%)                            │
   │   - MinHash-LSH against rolling buffer (Jaccard ≥ 0.85)           │
   │   - 13-gram decontamination against eval suite                    │
   │                                                                   │
   │ T+2:55  blend (the 70/20/10/5 rule)                                │
   │   - 65% tonight's new                                             │
   │   - 20% rolling 30-day replay (MSSR-weighted)                     │
   │   - 10% general anchor (FROZEN: Tulu 3 + OpenHermes-2.5 + ...)    │
   │   - 5% safety anchor (FROZEN: Aegis 2.0 + HelpSteer3 + NemoGuard) │
   │   - curriculum-within-pack guarantee                              │
   │                                                                   │
   │ T+3:00  pack to parquet                                           │
   │   - nemotron nano3 data prep sft  (Nemotron path)                 │
   │   - Unsloth packs on-the-fly      (Qwen3/Gemma4 path)              │
   │                                                                   │
   │ T+3:30  train                                                     │
   │   - Unsloth LoRA (Qwen3/Gemma4)  ~5h                               │
   │   - or Megatron-Bridge nano-v3 (Nemotron)  ~5–6h                   │
   │                                                                   │
   │ T+8:30  eval-gate                                                 │
   │   - MMLU-Pro-250, HumanEval, MBPP, SWE-Bench-Lite-50, BFCL v4,    │
   │     IFEval, MT-Bench, Arena-Hard-100, internal gold set           │
   │   - Bonferroni corrected                                          │
   │   - tool_call_compliance ≥ 0.995 strict                           │
   │                                                                   │
   │ T+8:50  forgetting probe                                          │
   │   - 100-prompt frozen set per base; judge-scored                  │
   │   - 1pp warn / 2pp/7d bump anchor / 3pp/7d suspend / 5pp rollback │
   │                                                                   │
   │ T+9:00  promote OR rollback                                       │
   │   - vLLM hot-swap load_lora_adapter                               │
   │   - POST adapter metadata + event to knowledge-base               │
   └──────────────────────────────────────────────────────────────────┘
```

## Repo layout (`~/lamark-trainer/`)

```
lamark-trainer/
├── pyproject.toml
├── lamark_trainer/
│   ├── __init__.py
│   ├── cli.py                           # `lamark-train` entry
│   ├── connectors/
│   │   ├── lamark_trace.py              # reads ~/.lamark/traces/ + KB
│   │   ├── git_mining.py
│   │   ├── pr_ingest.py
│   │   ├── youtrack_ingest.py
│   │   ├── jira_ingest.py
│   │   └── slack_ingest.py
│   ├── redact/
│   │   ├── stage1_secrets.py
│   │   ├── stage2_pii.py
│   │   └── substitution_map.py
│   ├── transform/
│   │   ├── trace_to_messages.py         # reducer is in lamark-trace; this consumes its output
│   │   ├── git_to_messages.py
│   │   ├── pr_to_messages.py
│   │   ├── issue_to_messages.py
│   │   └── slack_to_messages.py
│   ├── curate/
│   │   ├── oss_instruct.py
│   │   ├── llm_judge.py
│   │   └── exec_filter/
│   │       ├── runner.py
│   │       └── Dockerfile
│   ├── quality/
│   │   ├── ppl_filter.py
│   │   ├── minhash_dedup.py
│   │   ├── decontaminate.py
│   │   ├── length_cap.py
│   │   └── language_balance.py
│   ├── buffer/
│   │   ├── anchors_build.py             # one-shot; quarterly
│   │   ├── reservoir.py
│   │   └── mssr.py
│   ├── blend/
│   │   └── build_nightly_blend.py
│   ├── pack/
│   │   └── pack_parquet.py
│   ├── train/
│   │   ├── unsloth_lora.py              # Qwen3/Gemma4
│   │   ├── megatron_bridge.py           # Nemotron-3-Nano
│   │   ├── eager_load_patch.py          # DGX Spark UMA fix
│   │   └── dpo.py                       # weekly
│   ├── eval/
│   │   ├── gold_set/
│   │   ├── benchmarks/
│   │   ├── forgetting_probe/
│   │   │   ├── qwen36_probe.jsonl
│   │   │   ├── nemotron3_probe.jsonl
│   │   │   └── gemma4_probe.jsonl
│   │   ├── run_eval_gate.py
│   │   ├── run_forgetting_probe.py
│   │   └── thresholds.yaml
│   ├── serve/
│   │   ├── promote.sh
│   │   ├── rollback.sh
│   │   └── adapter_router.py
│   ├── prompt_opt/
│   │   ├── opro.py                      # plan/07b Loop C
│   │   ├── protegi.py
│   │   └── trial_run.py
│   └── kb_client.py                      # Python KB client (mirrors Rust shape)
└── tests/
```

## Data flow with knowledge-base

KB is the canonical store; the trainer pulls from KB and POSTs results back:

| Step | KB call |
|---|---|
| collect.lamark | `GET /agents/{id}/traces?since=...` → list+download reduced bundles |
| collect.episodes | `GET /memory/episodes?kind=reflexion&since=...` for Loop B lessons |
| eval.gold_set | `GET /knowledge/eval_sets?tag=gold` to seed eval/ |
| eval.forgetting_probe | `GET /knowledge/eval_sets?tag=probe` |
| promote | `POST /agents/{id}/adapters` with metadata + lineage |
| promote.event | `POST /agents/{id}/events` `kind=AdapterPromoted` |
| rollback.event | `POST /agents/{id}/events` `kind=AdapterRolledBack` |
| prompt_opt.outcomes | `GET /agents/{id}/sections/{sid}/outcomes?since=...` |
| prompt_opt.variant | `POST /knowledge/prompt_sections/{id}/variants` |
| prompt_opt.trial | `POST /knowledge/prompt_section_trials` |

## Connectors (key details)

### `lamark_trace` connector

```python
def collect_lamark_traces(since: datetime) -> Iterable[Conversation]:
    # 1. Read local: every reduced/conversation.jsonl in ~/.lamark/traces/ younger than `since`.
    # 2. Read KB: GET /agents/{id}/traces?since=... → fetch any not present locally.
    # 3. Deduplicate by rollout_id.
    for line in iter_jsonl(local_paths + kb_paths):
        yield Conversation.from_nemotron_agentic_v1(line)
```

Each `conversation.jsonl` line is one rollout — matches setup-guide §2.1.

### `git_mining`

OctoPack pattern (per setup-guide §3.1). Filter: ≥ 30 char message, ≤ N files changed, language-detected, no merge commits.

### `pr_ingest`

Reconstructs PR diffs as search/replace edit blocks (arxiv 2602.07457). Includes reviewer comments as second turn.

### `youtrack_ingest` / `jira_ingest`

Issue + linked commits + linked PRs → bug-fix trajectory. Issue link patterns: `Closes #N`, `<PROJ>-N`, branch-name prefixes.

### `slack_ingest`

Thread anchors (`thread_ts`). Filters: technical channels only; ≥ 1 reaction or "accepted" mark; redaction-as-usual.

## Redaction (mandatory)

```python
# stage1_secrets.py
def redact_secrets(input_jsonl, output_jsonl, audit_jsonl):
    """gitleaks + trufflehog + detect-secrets.
    Any verified finding → drop the sample and log the doc id to a quarantine list.
    """
    ...

# stage2_pii.py
def redact_pii(input_jsonl, output_jsonl, substitution_map):
    """Presidio + spaCy en_core_web_lg + GLiNER.
    Type-preserving substitution; consistent IDs within doc; salted; reset across docs.
    """
    ...
```

Audit log of every substitution → `~/.lamark/training/redacted/audit/<source>-<date>.jsonl`.

## Curation (optional, post-redaction)

Skippable in month 1 (per P0.1 decision). When enabled:

- **OSS-Instruct** — seed real redacted snippets to a frontier model; ask for diverse new instructions.
- **Two-judge** — score 5 axes; both ≥ 4/5.
- **Exec-filter** — sandbox parse + typecheck + ≥ 1 generated test.

## Quality / dedup / decontaminate

- PPL outlier — score with the base model itself; drop top + bottom 5%.
- MinHash-LSH — `datasketch`; Jaccard ≥ 0.85 across rolling buffer.
- 13-gram contamination — versus MMLU, GSM8K, HumanEval, MBPP, LiveCodeBench, SWE-Bench, BFCL.
- Length cap — drop > 16K tokens (unless long-context goal).
- Language balance — non-English ≤ 15% unless team operates in another language.
- Residual PII rescan — re-run Stage-2 after curation (frontier models can hallucinate real credentials).

## Buffer + blend

`buffer/anchors_build.py` is one-shot, quarterly. See [`plan/10b-dataset-catalog.md`](./10b-dataset-catalog.md) for the full sourcing details and HuggingFace pointers.

**General anchor** (~26 K samples, frozen):

| Dataset | Samples | HF path | Purpose |
|---|---|---|---|
| Tulu 3 SFT | ~10K | `allenai/tulu-3-sft-mixture` | Diverse instruction anchor (IFEval, FLAN, code, math, multilingual) |
| OpenHermes 2.5 | 10K (sampled) | `teknium/OpenHermes-2.5` | General chat + instruction diversity; MIT |
| IFEval train split | ~500 | `google/IFEval` | Instruction-following compliance lock |
| NuminaMath-CoT | ~2K | `AI-MO/NuminaMath-CoT` | Math reasoning anchor |
| CodeFeedback-Filtered | ~2K | `m-a-p/CodeFeedback-Filtered-Instruction` | Code generation anchor |
| Internal gold set | ~1K | `GET /knowledge/eval_sets?tag=gold` | Project-specific task lock |

**Safety anchor** (~3 K samples, frozen):

| Dataset | Samples | HF path | Purpose |
|---|---|---|---|
| Aegis 2.0 | ~1.5K | `nvidia/Aegis-AI-Content-Safety-Dataset-2.0` | Content-safety refusals |
| HelpSteer3 | ~1K | `nvidia/HelpSteer3` | Helpfulness + harmlessness preference |
| NemoGuard refusals | ~500 | `nvidia/NemoGuard-1.0-ContentSafety` | On-topic refusal patterns |

`buffer/reservoir.py`: reservoir sample new data into the rolling-30d buffer; cap at 10K; hard-evict > 30 days.

`buffer/mssr.py`: per-sample forgetting-risk score = `current_model.loss(s) - prev_model.loss(s)`. Weighted sample 0.7 * softmax(risk) + 0.3 * uniform.

`blend/build_nightly_blend.py`: 65/20/10/5 with curriculum-in-pack guarantee.

## Blend configuration

The blender reads `~/.lamark-trainer/blend_config.toml`:

```toml
[blend]
new_data_pct       = 65   # tonight's new traces
replay_pct         = 20   # 30-day MSSR-weighted replay
general_anchor_pct = 10   # frozen general anchor (Tulu 3 + OpenHermes-2.5)
safety_anchor_pct  = 5    # frozen safety anchor (Aegis 2.0 + HelpSteer3)

[blend.anchors]
general = [
  "~/.lamark-trainer/anchors/tulu3.parquet",
  "~/.lamark-trainer/anchors/openhermes25.parquet",
  "~/.lamark-trainer/anchors/ifeval_train.parquet",
  "~/.lamark-trainer/anchors/numinamath_cot.parquet",
  "~/.lamark-trainer/anchors/codefeedback.parquet",
  "~/.lamark-trainer/anchors/gold_set.parquet",
]
safety  = [
  "~/.lamark-trainer/anchors/aegis2.parquet",
  "~/.lamark-trainer/anchors/helpsteer3.parquet",
  "~/.lamark-trainer/anchors/nemoguard.parquet",
]

[blend.anchor_bump]
bump_general_on_sustained_regression = 2   # pp to add per 7-day period
bump_safety_on_sustained_regression  = 1
max_combined_anchor_pct              = 30  # cap: general + safety ≤ 30%
reset_on_consecutive_passes          = 3   # revert bumps after N consecutive clean forgetting probes
```

**Normalization:** after any bump, percentages are renormalized to sum to 100% by reducing `new_data_pct` first, then `replay_pct`. If `new_data_pct` would fall below 40%, the bump is capped and an operator alert is emitted. The config is written atomically (tmp + rename) by the forgetting probe on anchor-bump events.

## Training

Two paths, both ingest the same packed parquet. A third path — RL (GRPO) — is activated after SFT produces a competent adapter; see [`plan/07b §Loop D`](./07b-prompt-self-improvement.md#loop-d--rl-phase-guided-then-grpo-post-sft-bootstrapping).

**Key training research:**
- SFT loop = STaR (arXiv 2203.14465) + ReST (arXiv 2308.08998) + ReST-meets-ReAct (arXiv 2312.10003)
- RL phase = DeepSeek-R1 GRPO (arXiv 2501.12948) + Agent-RLVR (arXiv 2506.11425)
- Verifier = V-STaR (arXiv 2402.06457) joint generator+verifier training
- Error recovery training = Fission-GRPO (arXiv 2601.15625)
- Step-level reward = PRM "Let's Verify Step by Step" (arXiv 2305.20050)

### Unsloth path (Qwen3 / Gemma4)

```python
from unsloth import FastModel
model, tokenizer = FastModel.from_pretrained(
    model_name="Qwen/Qwen3.6-35B-A3B",
    max_seq_length=4096,
    load_in_16bit=True,
    full_finetuning=False,
)
# rank 32, alpha 64, lr 1e-4
# target = q/k/v/o + gate/up/down + (MoE-specific) expert_gate
```

### Megatron-Bridge path (Nemotron-3-Nano)

```bash
python -m megatron_bridge.examples.recipes.nemotron_3.finetune_nemotron_3_nano \
    --per-split-data-args-path /training/data/data_args.json \
    --tokenizer-model /models/nemotron3/tokenizer.model \
    --config-file configs/spark_single.yaml
```

### DGX Spark UMA fix

`train/eager_load_patch.py` per setup-guide §4.3. Required on Spark to bypass the 66%-of-weight-load OOM.

## Adapter versioning

```
{base}-{mode}-{YYYYMMDD}-{git_sha}.safetensors
```

Last 7 nights + weekly snapshots retained on disk. KB stores metadata + lineage.

## Eval gate (`eval/thresholds.yaml`)

```yaml
must_pass_all:
  mmlu_pro_250:        { drop_pp_max: 1.0 }
  mt_bench:            { drop_pct_max: 5 }
  ifeval:              { drop_pct_max: 2 }
  humaneval:           { drop_pp_max: 0 }
  swebench_lite_50:    { drop_issues_max: 1 }
  internal_gold:       { improve_pp_min: 2 }
  tool_call_compliance:{ min_rate: 0.995 }
  forgetting_probe:    { drop_pp_max_7d: 2 }
bonferroni_correction: true
```

## Forgetting probe

`eval/forgetting_probe/{base}_probe.jsonl` — 100 prompts each, drawn from the original SFT mix, never trained on, never rotated.

Drift triggers per setup-guide §3.5.7:
- 1pp single night → warn only.
- 2pp/7d → bump anchor 10% → 15% in tomorrow's blend.
- 3pp/7d → suspend nightly cycles; escalate to monthly merge.
- 5pp anywhere → rollback to last weekly snapshot + audit pipelines.

## KB forgetting probe event schemas

All three forgetting-probe outcome events share a common base schema posted to `POST /agents/{id}/events`:

```json
{
  "kind": "ForgettingWarn | AdapterSuspended | AdapterRejected",
  "adapter_id": "<adapter UUID>",
  "base_model_id": "<base model ID>",
  "probe_run_id": "<UUID>",
  "probe_set_hash": "<SHA-256 of probe set>",
  "timestamp": "<ISO8601>",
  "per_bucket_deltas": {
    "reasoning": -0.8,
    "code": -1.2,
    "instruction_following": 0.1,
    "safety": 0.0,
    "factual_recall": -0.5
  },
  "worst_bucket_delta": -1.2,
  "threshold_tier": "warn | bump_anchor | suspend | rollback",
  "rolling_7d_baseline": { "reasoning": 78.3 }
}
```

Additional fields per event kind:

- **`ForgettingWarn`** — adds `"days_since_last_warn": N`.
- **`AdapterSuspended`** — adds `"suspension_reason": "single_day_2pp | rolling_3pp_7d"`.
- **`AdapterRejected`** — adds `"rollback_reason": "single_day_5pp"`, `"previous_adapter_id"`, and `"forced": true | false` (whether sessions were active during rollback).

## Promote / rollback

```bash
# promote (eval green)
curl -X POST http://vllm:8000/v1/load_lora_adapter \
    -d "{\"lora_name\":\"lamark-coder\",\"lora_path\":\"/adapters/$NEW\"}"
curl -X POST http://vllm:8000/v1/unload_lora_adapter \
    -d "{\"lora_name\":\"lamark-coder-yesterday\"}"
```

KB event posted: `kind=AdapterPromoted` with lineage (dataset_id, training_run_id, eval_results, forgetting_probe_score).

If eval fails: `kind=AdapterRejected`; failure trajectories queued for tomorrow's shard; `exit 1` triggers the cron alert.

**Rollback deferral:** before calling `vLLM unload_lora_adapter` during a rollback, the rollback executor checks active session count via `GET /v1/adapters/{adapter_id}/active_sessions`. If `count > 0`, rollback is deferred (polls every 10 s, up to `rollback_drain_timeout_seconds = 1800`). After the timeout, forced rollback proceeds and `AdapterRejected.forced = true` is set. Full algorithm is specified in `plan/05c § Rollback deferral for active sessions`.

## Weekly DPO (Sundays)

```python
def build_preference_pairs():
    # 1. Same-prompt reruns of last week's failed sessions against current adapter.
    # 2. PermissionDenied events from the runtime → rejected branch.
    # 3. Two-judge re-scoring of regenerated candidates.
    # 4. KB reinforce signal: positive vs negative outcomes for the same prompt class.
```

TRL `DPOTrainer` on the SFT LoRA, +1 epoch. Same eval gate.

## DPO pair schema

The `reduced/dpo_pairs.jsonl` file uses Nemotron-Agentic-v1 format. Each line is one preference pair:

```json
{
  "pair_id": "<SHA-256 of session_id + decision_point_turn_id>",
  "session_id": "<rollout_id>",
  "decision_point": {
    "turn_id": "<turn_id at which trajectories diverge>",
    "decision_type": "tool_call_recovery | agent_decision | orchestration_decision",
    "evidence": "<brief human-readable description of the evidence that the rejected trajectory would fail>"
  },
  "chosen": {
    "trajectory_id": "<pair_id>_c",
    "messages": []
  },
  "rejected": {
    "trajectory_id": "<pair_id>_r",
    "messages": [],
    "projection_method": "replay_without_recovery | counterfactual_continuation"
  },
  "admissibility": "grounded",
  "reducer_version": "<semver>"
}
```

`chosen.messages` and `rejected.messages` are Nemotron-Agentic-v1 message arrays from `decision_point.turn_id` onward.

**Admissibility rule:** `admissibility` is always `"grounded"` in v0.1. A rejected trajectory is only emitted when the coordinator/agent had **observational evidence** at decision time (heartbeat miss, error response, explicit failure). Speculative rejections are NOT emitted.

**Projection methods:**
- `replay_without_recovery` — the trace was replayed without the recovery branch.
- `counterfactual_continuation` — the failing path was extended by projecting forward from the last known state.

## Monthly merge (day 30)

```python
def monthly_merge():
    model = load_base_with_lora(prod_adapter)
    merged = model.merge_and_unload()
    requantize(merged, target="AWQ-INT4")  # or NVFP4 via TensorRT Model Optimizer
    reset_lora_delta(merged)
    recompute_ewc_fisher(merged, anchor=general_anchor)
    refresh_oss_instruct_seeds()
    rotate_frontier_teacher()  # Claude → GPT → Gemini
    full_eval_sweep()
    tag = f"lamark-base-v{datetime.utcnow():%Y.%m}"
    kb.upsert_release(tag, lineage)
```

## Loop C (prompt section evolution) jobs

Scheduled weekly, separate from the SFT/DPO cycle:

```python
def section_evolution_run():
    flagged = kb.flagged_sections(window_days=7)
    for s in flagged:
        outcomes = kb.section_outcomes(s.id, window_days=7)
        variants = opro_propose(s, outcomes, k=8)         # frontier model
        survivors = protegi_score_and_prune(variants, holdout=200, keep=2)
        for v in survivors:
            if forgetting_probe_shadow(v).drop_pp < 2:
                kb.post_variant(s.id, v)
                kb.start_trial(s.id, baseline=s.current_version, variant=v)
```

## Cron orchestration (`/etc/cron.d/lamark-trainer`)

```
0 0  * * *   mluser cd ~/lamark-trainer && uv run lamark-train nightly    >> /var/log/lamark/nightly.log 2>&1
0 1  * * 0   mluser cd ~/lamark-trainer && uv run lamark-train weekly     >> /var/log/lamark/weekly.log  2>&1
0 2  1 * *   mluser cd ~/lamark-trainer && uv run lamark-train monthly    >> /var/log/lamark/monthly.log 2>&1
0 3  * * 1   mluser cd ~/lamark-trainer && uv run lamark-train opt-prompts >> /var/log/lamark/opt.log   2>&1
*/5 * * * *  mluser cd ~/lamark-trainer && uv run lamark-train health     >> /var/log/lamark/health.log 2>&1
```

`nightly`, `weekly`, `monthly`, `opt-prompts` are CLI subcommands of `lamark-train`. **Fail-fast** on any non-zero exit.

## Observability

- W&B Weave or Langfuse — every adapter run logs loss curves, eval scores, regression reports, mix-ratios.
- Drift detector — sliding window of last 10K production prompts; `intfloat/multilingual-e5-large`; alert on embedding-KL > 2σ over 7-day.
- Mix-ratio dashboard — chart actual 65/20/10/5; flag ±5%.
- Forgetting-probe trend chart with the 1/2/3/5pp threshold lines drawn in.
- Prom metrics from `lamark-train` (push gateway).

## Failure modes

| Failure | Mitigation |
|---|---|
| KB unreachable during pull | Fallback to local `~/.lamark/traces/` only; warn in run report. |
| Frontier API rate-limited | Hard 10rps cap + jitter; bail on persistent rate limit. |
| Spark OOM at 66% weight load | `eager_load_patch.py` (mandatory on Spark). |
| Eval suite flaky | Bonferroni + 3-run-median for the volatile benches. |
| Promote vLLM unreachable | Retry 5×; if still failing, leave yesterday's adapter live, alert. |
| Disk full on `/training` | Pre-flight free-space check (≥ 200GB required); skip-with-alert if low. |

## Cutover gate (P7 + P8 done)

- ✅ End-to-end nightly run on a tiny synthetic dataset (~100 samples) produces an adapter and a green eval-gate result in <2h on dev hardware.
- ✅ A planted regression (e.g., training on samples with `tool_call_compliance=0.5`) is caught by the gate and rolled back; vLLM stays on yesterday.
- ✅ Forgetting-probe drift > 2pp/7d triggers anchor bump in tomorrow's blend, observable in the mix-ratio dashboard.
- ✅ KB roundtrip: adapter promotion event + lineage retrievable via `GET /agents/{id}/adapters/{id}/lineage`.
- ✅ Weekly DPO completes and produces a DPO-tuned LoRA distinct from the SFT one.
- ✅ 14 consecutive unattended nights with no rollback alerts on the dev box.
