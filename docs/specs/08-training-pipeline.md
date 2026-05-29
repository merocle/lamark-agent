# 08 — Training pipeline (policy)

> This page holds the **policy** (blend ratios, training tiers, non-negotiable
> constraints, eval gates, forgetting-probe triggers — "what we promise"). The
> **operational** pipeline (stages, connectors, cron, serving commands) lives in
> [`../plan/10-training-pipeline.md`](../plan/10-training-pipeline.md) and the
> dataset catalog in [`../plan/10b-dataset-catalog.md`](../plan/10b-dataset-catalog.md).

## Sources

1. **Lamark trace bundles** — primary signal; the reducer emits Nemotron-Agentic-v1 directly (see [`06-trace-and-data-format.md`](./06-trace-and-data-format.md)).
2. `git_mining` — OctoPack pattern (commit message + diff → instruction pair).
3. `pr_ingest` — multi-turn search/replace edits from PR review history.
4. `youtrack_ingest` / `jira_ingest` — issue → bug-fix trajectory.
5. `slack_ingest` — thread → Q&A pair.
6. **`codebase_extract`** (see [`../plan/10c-dataset-from-codebase.md`](../plan/10c-dataset-from-codebase.md)) — static-analysis bug-fix pairs from git history; enriched with MR/PR trajectory → teacher traces → RL task triplets.

Mandatory two-stage redaction (Gitleaks+TruffleHog+detect-secrets, then Presidio+spaCy+GLiNER with consistent salted IDs). Curation (post-redaction only): OSS-Instruct seeding + two-judge consensus + execution-based filter for code. Quality: perplexity outlier, MinHash dedup, 13-gram decontamination, language balance, length cap, residual-PII rescan.

## Blend (70/20/10/5 rule)

| Bucket | Share | Source | Notes |
|---|---|---|---|
| Today's new task data | 65% | tonight's curated traces + git/PR/issue/slack | knowledge-base `GET /knowledge/datasets?since=...` |
| Rolling task replay | 20% | reservoir-sampled last 30 nights | **MSSR-weighted** by forgetting risk |
| General anchor | 10% | Tulu 3 + OpenHermes-2.5 + IFEval + math/code/chat | **FROZEN** (quarterly refresh only) |
| Safety anchor | 5% | Aegis 2.0 + HelpSteer3 + NemoGuard | **FROZEN** |

Curriculum-within-pack: every 4096-token pack contains ≥1 anchor + ≥1 replay + remainder new.

## Training tiers — decision matrix

| Tier | Method | Frequency | When to use | v0.1 status |
|---|---|---|---|---|
| **0 — CPT** | LoRA r=128 + `embed_tokens`/`lm_head` on BASE; ~5% pretrain replay | Ad hoc | Raw domain corpus > 50 MB that can't be Q&A. Skip in 99% of cases. | Skip unless explicitly needed |
| **0.5 — Skill + Harness evolution** | SkillOpt loop + LIFE-HARNESS layer evolution from traces | Weekly (Sat) | Always — fixes 90% of interface failures at zero weight cost | **Active** |
| **1 — SFT LoRA** | LoRA r=16–32 on INSTRUCT; `assistant_only_loss=True`; **reasoning failures only (~10%)** | Nightly | Residual capability after harness+skills can't fix it | **Active** |
| **2 — DPO** | LoRA r=16 on promoted SFT adapter; `β=0.1, lr=5e-6`; grounded preference pairs | Weekly (Sun) | Alignment polish; reduces hallucination | **Active** |
| **3 — GRPO/RLVR** | Synchronous GRPO with task verifiers | Monthly once stable | After SFT stable (≥30 clean nights) + verifiers | **v0.2+ roadmap** |
| **4 — Full weight FT** | All params; Megatron-Bridge TP=2, EP=8 | Never in cycle | From-scratch reproductions only — out of scope | **Out of scope** |

## Non-negotiable SFT constraints (every run)

- `load_in_4bit=False` for MoE (Qwen3.6, Nemotron); use `load_in_16bit=True` bf16. QLoRA OOMs at ~4% of weight load on Spark.
- `assistant_only_loss=True` — mandatory; prevents learning user/system token patterns.
- Never tune `lm_head` or `embed_tokens` during SFT (only for CPT on BASE).
- Never tune the MoE router.
- `α = r` (conservative forgetting) or `α = 2r` (aggressive). Never arbitrary alpha.
- Rank 16 safe default (less forgetting); go to 32 only if validation loss plateaus.
- DoRA (`use_dora=True`) gives +0.3–4 pp at low ranks; rsLoRA only matters at r ≥ 64.

## Eval gates (`eval/thresholds.yaml`)

```yaml
# Nemotron official eval suite (Nano 30B-A3B BF16 baseline for reference):
#   bfcl_v4: 53.8% | livecodebench_v6: 68.3% | mmlu_pro: 78.3% | gpqa: 73.0%
must_pass_all:
  mmlu_pro_250:        { drop_pp_max: 1.0 }
  mt_bench:            { drop_pct_max: 5 }
  ifeval:              { drop_pct_max: 2 }
  humaneval:           { drop_pp_max: 0 }
  swebench_lite_50:    { drop_issues_max: 1 }
  bfcl_v4:             { drop_pp_max: 1.0 }     # tool-call compliance
  tool_call_compliance:{ min_rate: 0.995 }
  internal_gold:       { improve_pp_min: 2 }
  forgetting_probe:    { drop_pp_max_7d: 2 }
bonferroni_correction: true
```

**Forgetting-probe triggers:** 1pp single-night warn / 2pp 7d auto-bump anchor / 3pp 7d suspend / 5pp anywhere rollback.

**Weekly DPO** (Sundays): preference pairs from same-prompt reruns (two-judge), PermissionDenied events (rejected branch), and KB `reinforce` signal. LR 5e-6. Same eval gate; DPO failure → keep SFT adapter live.

**Monthly merge** (day 30): `merge_and_unload` → requantize → reset LoRA delta → recompute EWC Fisher → rotate frontier teacher → full eval sweep → tag `lamark-base-vYYYY.MM`. Merge-without-gradient; no training steps occur.
