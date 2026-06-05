# 10 — Training pipeline (Python)

> The only Python in the project. Runs on the GPU box. Pulls traces from
> `~/.lamark/traces/` and `../knowledge-base`, produces LoRA adapters, gates
> them through evals, promotes or rolls back.

**Code location:** sibling project `~/lamark-trainer/` (not in this cargo workspace).
**Data location:** `~/.lamark/training/` (mirrors hermes operational layout).
**Reference:** `setup-guide.md` (Parts 1–4; also in `~/Downloads/`) — operational truth for the nightly runbook; this file is the project-side spec. Additional context: `compass_artifact_wf-71178c8c` (Nemotron-3-Nano architecture + DGX Spark deployment), `compass_artifact_wf-9b64b930` (Qwen3 family fine-tuning on 12 GB RTX). For the codebase-extraction + RL-task pipeline, see [`plan/10c`](./10c-dataset-from-codebase.md).

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
   │   - codebase_extract --since=yesterday     →  /raw/codebase.jsonl │
   │     (static-analysis bug-fix pairs; see plan/10c)                 │
   │   - mr_enrich (enriches codebase.jsonl in-place)                  │
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
   │ T+1:15  teacher traces for codebase pairs (plan/10c Phase 3)      │
   │   - teacher_trace.py --teacher glm-4.6                           │
   │   - three-stage filter: bad verifier → env stability → difficulty │
   │   - output: /raw/codebase_sft.jsonl (Nemotron-Agentic-v1)        │
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
│   │   ├── git_mining.py                # OctoPack-style commit→instruction
│   │   ├── pr_ingest.py                 # PR diffs as search/replace trajectories
│   │   ├── youtrack_ingest.py
│   │   ├── jira_ingest.py
│   │   ├── slack_ingest.py
│   │   ├── codebase_extract.py          # Phase 1: static-analysis bug-fix pairs (plan/10c)
│   │   ├── mr_enrich.py                 # Phase 2: MR/PR trajectory enrichment (plan/10c)
│   │   ├── teacher_trace.py             # Phase 3: GLM-4.6 teacher traces → SFT (plan/10c)
│   │   └── rl_task_build.py             # Phase 4: task triplets + GRPO groups (plan/10c)
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
│   │   ├── dpo.py                       # weekly
│   │   └── grpo.py                      # Tier 3 (v0.2+); NeMo RL / TRL GRPO
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

### Tier decision matrix

Choose the training tier based on what data you have and what you want to change. Always start from Tier 1; escalate only when needed.

| Tier | Method | When to use | Hardware (single Spark) | Scope in Lamark |
|---|---|---|---|---|
| **0 — CPT** | Continued pre-training on BASE model; LoRA r=128 + `embed_tokens`/`lm_head`; ~5% pretrain replay | Raw domain text > 50 MB that can't be expressed as Q&A pairs | ~24 h+; only viable for small models (≤8B) on Spark BF16 | Skip in 99% of cases; only if domain knowledge genuinely can't be injected via SFT |
| **1 — SFT LoRA** | LoRA r=16–32 on INSTRUCT checkpoint; 70/20/10/5 blend; `assistant_only_loss=True` | Nightly cycle; capability injection from traces + git/PR/issue/Slack | ~5–6 h | Primary tier; runs every night |
| **2 — DPO** | LoRA r=16 on top of promoted SFT adapter; `β=0.1`, `lr=5e-6`; preference pairs | Weekly (Sundays); alignment polish; reduces refusals/hallucinations | ~2 h | Weekly cycle |
| **3 — GRPO/RLVR** | Synchronous GRPO with task verifiers; NeMo RL (Nemotron) or TRL GRPO (Qwen3.6) | v0.2+ once SFT cycle is stable and NeMo Gym environments are wired | Single Spark possible but slower than SFT | Not in v0.1 scope |
| **4 — Full weight FT** | All parameters; Megatron-Bridge TP=2, EP=8, PP=1; AdamW | Never in the nightly cycle; only from-scratch reproductions | Not viable: 30B+ MoE exceeds Spark's 128 GB UMA including optimizer states | Out of v0.1 scope permanently |

**The instinct to reach for full FT or GRPO early is the most common mistake.** Most capability gains come from better SFT data, not a more powerful training algorithm. Fix the data before touching the tier.

---

### Implemented trainers (DGX Spark, Qwen3.5-9B) — what actually runs today

The tiers above are the target taxonomy. The scripts that exist and are validated
live in `learning/scripts/spark/`, all in the `lamark/sft:26.01` image
(transformers 5.9 + TRL + peft + torchao + causal-conv1d + flash-linear-attention).
Common knobs (env): `BATCH_SIZE/GRAD_ACCUM/GRAD_CKPT/PACK/DL_WORKERS/EPOCHS/LR`.

| Script | Method | Trains | Notes |
|---|---|---|---|
| `train_sft.py` | TRL `SFTTrainer` LoRA (r=32) | prose buckets ({conversations}) | simplest; `assistant_only_loss` (see caveat) |
| `train_sft_agentic.py` | LoRA + manual tokenize + `Trainer` | prose **+ native tool-call trajectories** (Nemotron-Agentic-v1 `{messages,tools}`) | teaches tool-call *emission*; the agentic format can't be a TRL/Arrow column, hence manual tokenize |
| `train_molf.py` (+ `molf.py`) | **MoLF-E** (arXiv:2605.07111) | frozen base + 2 LoRA experts (r=64/128), Sparse-AdamW EPD Top-1 routing | experimental; auto-navigates Fact-vs-Med (FFT-vs-LoRA) without picking; folds to a standard LoRA adapter on export. **Why not full MoLF / full FT:** the FFT expert needs ~144 GB optimizer state on a 9B → OOM on Spark; MoLF-E keeps LoRA-level memory. |

**Performance (data-driven).** A 9B LoRA used only ~26 GB of ~118 GB at batch=1.
The win is **packing** (concatenate the mostly-short rows into dense `max_length`
sequences — zero padding) + **flash-linear-attention** (fast gated-DeltaNet
kernels; without it transformers logs "fast path not available" and runs ~2×
slower). Together: a 2-epoch run ~83 min → ~24 min at ~72 GB. Naive batch-up
*without* packing regresses (pads short rows to the batch max). Wall-clock is
token-bound, so bigger batches mainly raise memory + cut step count.

**Caveats.** (1) `assistant_only_loss` is a **no-op** on Qwen3.5 — its chat
template lacks `{% generation %}`, so `return_assistant_tokens_mask` yields no
mask and training is full-sequence; fix with a response-template collator if it
matters. (2) Packing collapses the dataset to few optimizer steps — **raise
`EPOCHS`** or identity/knowledge undertrain. (3) The raw instruct `Qwen/Qwen3.5-9B`
**already emits native tool calls**; prose-only SFT degrades that, trajectory
training restores it. (4) Inference must use `device_map={"":0}` (auto-offload of
the conv layers crashes `causal_conv1d`).

---

### Tier 0 — CPT (skip unless truly needed)

For the rare case where you have a large unseen raw corpus (internal documentation, proprietary codebases, domain-specific pre-training text > 50 MB):

```python
# Stage 1: CPT on BASE model only
model, tokenizer = FastModel.from_pretrained(
    model_name="Qwen/Qwen3.6-35B-A3B",   # BASE, not instruct
    max_seq_length=4096,
    load_in_16bit=True,
    full_finetuning=False,
)
model = FastModel.get_peft_model(
    model, r=128, lora_alpha=128,          # high rank for CPT
    target_modules=["q_proj","k_proj","v_proj","o_proj",
                    "gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth",
)
# Enable embed + lm_head only for CPT; NEVER for SFT
model.enable_input_require_grads()
model.get_input_embeddings().requires_grad_(True)
model.lm_head.requires_grad_(True)
# ~5% pretrain-style replay mixed in; LR 1e-4, 1-3 epochs
```

Then proceed to Tier 1 SFT on the INSTRUCT checkpoint (NOT the CPT checkpoint). The CPT checkpoint is discarded after the SFT run; its purpose was only to inject raw domain tokens into the base before instruct alignment.

---

### Tier 1 — SFT LoRA (nightly)

**Non-negotiable settings (apply to all models):**
- `load_in_4bit=False` for MoE models (Qwen3.6, Nemotron); use `load_in_16bit=True` (bf16)
- `load_in_4bit=True` only for dense ≤8B (Qwen3-4B, Gemma4-9B)
- `assistant_only_loss=True` or Unsloth's `train_on_responses_only` — mandatory for multi-turn; adds ~1 pp and prevents the model from learning to predict user/system tokens
- Never tune `lm_head` or `embed_tokens` during SFT
- Never tune the MoE router weights (Qwen3.6 `router`, Nemotron shared/routed expert routing)
- `α = r` (Unsloth default, conservative) or `α = 2r` (aggressive); never arbitrary values

**Per-model hyperparameter table:**

| Model | r / α | Target modules | max_seq | Epochs | LR | Peak VRAM on Spark |
|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B | 32 / 32 | `q_proj,k_proj,v_proj,o_proj,in_proj,out_proj,gate_proj,up_proj,down_proj` | 4096 | 1–2 | 1e-4 | ~72 GB bf16 |
| Nemotron-3-Nano | 32 / 32 | `q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj` (skip router) | 4096 | 1–2 | 1e-4 | ~70–80 GB bf16 |
| Gemma4-27B | 32 / 32 | `q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj` | 2048 | 1–2 | 1e-4 | ~60 GB bf16 |
| **Qwen3.5-9B** (dense) | 32 / 32 | verify via `model.named_modules()` — DeltaNet layers have different names than attn | 4096 | 1–2 | 2e-4 | ~11 GB 4-bit |
| **Qwen3.5-4B** (dense) | 32 / 32 | same — verify DeltaNet target names before run | 4096 | 1–2 | 2e-4 | ~7–9 GB 4-bit |
| **Qwen3.5-2B** (dense) | 16–32 / =r | verify DeltaNet target names | 4096 | 2–3 | 2e-4 | ~4–5 GB 4-bit |
| **Qwen3.5-0.8B** (dense; MTP) | 16 / 16 | verify DeltaNet target names | 4096 | 2–3 | 2e-4 | ~2–3 GB 4-bit |

> **Qwen3.5 DeltaNet note:** always run `print([n for n,_ in model.named_modules()])` before setting `target_modules`. The SSM (DeltaNet) layers use projection names like `in_proj`, `out_proj`, or similar — they differ from standard Transformer `q_proj`/`k_proj`. Passing wrong names silently trains only the attention layers. Unsloth may auto-detect them; confirm with `model.print_trainable_parameters()` after `get_peft_model()`.

> **4-bit is safe for Qwen3.5 dense models** (unlike Qwen3.6 MoE where it OOMs). Use `load_in_4bit=True` for ≤9B. The Qwen3.5 architecture uses the same quantization sensitivity profile as Qwen3 dense models (not MoE).

**Rank guidance:** 16 is the safe default (Biderman et al. TMLR 2024: lower rank forgets ~9 pp less than higher rank). Increase to 32 only if validation loss plateaus. DoRA (`use_dora=True`) adds +0.3–4 pp especially at low ranks; rsLoRA (`use_rslora=True`) only helps at r ≥ 64.

**SFT LoRA snippet (Qwen3.6-35B-A3B, Unsloth path):**

```python
from unsloth import FastModel
from trl import SFTTrainer, SFTConfig

model, tokenizer = FastModel.from_pretrained(
    model_name="/models/qwen36",
    max_seq_length=4096,
    load_in_4bit=False,
    load_in_16bit=True,      # bf16; QLoRA explicitly NOT supported for qwen3_5_moe
    full_finetuning=False,
)
model = FastModel.get_peft_model(
    model, r=32, lora_alpha=32,   # α = r (Unsloth recommendation)
    target_modules=[
        "q_proj","k_proj","v_proj","o_proj",
        "in_proj","out_proj",            # Qwen3.6 MoE expert projections
        "gate_proj","up_proj","down_proj",
    ],
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer,
    train_dataset=train_ds, eval_dataset=eval_ds,
    args=SFTConfig(
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,  # eff bs=16
        warmup_ratio=0.05,
        num_train_epochs=1,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        optim="adamw_8bit",
        weight_decay=0.01,
        max_seq_length=4096,
        assistant_only_loss=True,        # NON-NEGOTIABLE
        bf16=True,
        eval_strategy="steps", eval_steps=100,
        load_best_model_at_end=True,
    ),
)
trainer.train()
```

**SFT LoRA snippet (Nemotron-3-Nano, Megatron-Bridge path):**

```bash
# spark_single.yaml sets TP=1, EP=1 (single device), bf16
python -m megatron_bridge.examples.recipes.nemotron_3.finetune_nemotron_3_nano \
    --per-split-data-args-path /training/data/data_args.json \
    --tokenizer-model /models/nemotron3/tokenizer.model \
    --config-file configs/spark_single.yaml
```

`spark_single.yaml` key overrides for single-Spark:
```yaml
model_parallel_size: 1
expert_parallel_size: 1
pipeline_parallel_size: 1
micro_batch_size: 1
global_batch_size: 16
lr: 1.0e-5          # Megatron convention: LR 1e-5 for full-context packing
min_lr: 1.0e-6
train_iters: 2000
eval_iters: 50
lora:
  r: 32
  alpha: 32
  target_modules: [q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj]
  # never add router_weight here
```

**DGX Spark UMA fix (mandatory):**

`train/eager_load_patch.py` — must be applied before loading any BF16 model on Spark to prevent OOM at ~66% of weight load:

```python
import os
from safetensors.torch import safe_open

def patched_load_shard(path: str, device: str) -> dict:
    tensors = {}
    with safe_open(path, framework="pt", device=device) as f:
        for k in f.keys():
            tensors[k] = f.get_tensor(k).to(device, non_blocking=True)
    # evict page cache to recover UMA immediately after load
    fd = os.open(path, os.O_RDONLY)
    os.posix_fadvise(fd, 0, os.stat(path).st_size, os.POSIX_FADV_DONTNEED)
    os.close(fd)
    return tensors
```

After loading, also run `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'` to clear Linux page cache competing with model memory in the unified pool.

---

### Tier 2 — DPO (weekly, Sundays)

Runs on top of the week's promoted SFT adapter. Preference pairs are grounded in observed evidence (never speculative).

```python
from trl import DPOTrainer, DPOConfig

trainer = DPOTrainer(
    model=model, ref_model=ref_model,
    train_dataset=dpo_pairs_ds,
    args=DPOConfig(
        beta=0.1,
        learning_rate=5e-6,          # lower LR than SFT (alignment, not capability)
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        optim="adamw_8bit",
        bf16=True,
        lr_scheduler_type="linear",
        max_length=4096,
        max_prompt_length=2048,
    ),
)
```

Same eval gate as SFT. If DPO fails the gate, the SFT adapter stays in production; the DPO failure trajectories are queued for the next week's shard.

---

### Tier 3 — GRPO/RLVR (v0.2+, not in v0.1)

Prerequisites before activating:
1. SFT nightly cycle has been stable for ≥ 30 consecutive nights (no forgetting probe alerts).
2. Internal gold set shows ≥ 5 pp improvement over the pre-nightly-SFT baseline.
3. Task verifier functions are implemented per environment type:
   - **Math:** exact-answer match or SymPy equivalence.
   - **Code:** execution in Docker sandbox + ≥ 1 generated unit test passes.
   - **Tool-use:** full round-trip (tool call → valid response → assistant summarizes correctly).
   - **Instruction-following:** IFEval-style format verifier.

**Nemotron path:** `NVIDIA-NeMo/Gym` environments + `NVIDIA-NeMo/RL` synchronous GRPO. Container: `nvcr.io/nvidia/nemo-rl:v0.4.0.nemotron_3_nano`. Dataset: `nvidia/Nemotron-3-Nano-RL-Training-Blend` + Lamark's own `codebase_rl_tasks.jsonl` (from plan/10c Phase 4).

**OpenThoughts path (Harbor + SkyRL):** Use the `open-thoughts/OpenThoughts-Agent-v1-RL` task format (instruction.md + Dockerfile + verifier.py) produced by `connectors/rl_task_build.py`. Orchestrate with Harbor (`github.com/harbor-framework/terminal-bench`) for container execution and SkyRL (`github.com/NovaSky-AI/SkyRL`) for the RL training loop. This path is model-agnostic and works for both Qwen3.6 and Nemotron.

**Qwen3.6 path:** TRL `GRPOTrainer` on packed Parquet. Works on single Spark but runs materially slower than SFT (~3× wall-clock per step). Budget accordingly.

**Key GRPO hyperparameters:** `lr=1e-6` (lower than SFT — forgetting is catastrophic at GRPO LR), `kl_coeff=0.1`, `clip_ratio=0.2`, `num_generations=8` per prompt. **Do not run GRPO until the SFT cycle is stable** — GRPO on unstable base behavior produces reward hacking rather than genuine improvement.

**Key GRPO literature:**
- SFT loop = STaR (arXiv 2203.14465) + ReST (arXiv 2308.08998) + ReST-meets-ReAct (arXiv 2312.10003)
- RL phase = DeepSeek-R1 GRPO (arXiv 2501.12948) + Agent-RLVR (arXiv 2506.11425)
- Verifier design = V-STaR (arXiv 2402.06457)
- Error recovery = Fission-GRPO (arXiv 2601.15625)
- Step-level reward = PRM "Let's Verify Step by Step" (arXiv 2305.20050)

---

### Tier 4 — Full weight fine-tuning (out of scope)

| Model | Why not viable on single Spark | Minimum hardware |
|---|---|---|
| Nemotron-3-Nano (30B MoE) | BF16 weights (~62 GB) + activations + AdamW optimizer states (2× model for fp32 Adam) exceed 128 GB UMA | ≥ 2× H100 nodes; Megatron-Bridge TP=2, EP=8, PP=1 |
| Qwen3.6-35B-A3B | Same capacity constraint | ≥ 2× H100 nodes; Megatron-SWIFT EP=8 |
| Gemma4-27B | BF16 (~54 GB) + Adam states (~108 GB) exceeds Spark | Single H100 80 GB (tight); dual Spark via ConnectX-7 might work |

Full weight training is never part of the nightly, weekly, or monthly cycles. The monthly merge (`merge_and_unload`) merges LoRA deltas back into frozen base weights — this is merge-without-gradient, not training. True full-parameter retraining from scratch requires a multi-node H100 cluster and is out of scope for v0.1 and likely v0.2.

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

TRL `DPOTrainer` on the promoted SFT LoRA. Key settings: `beta=0.1`, `lr=5e-6` (lower than SFT — alignment polish, not capability injection), `num_train_epochs=1`, `bf16=True`, `max_length=4096`, `max_prompt_length=2048`. Same eval gate. If DPO fails the gate, the SFT adapter stays in production; DPO failure trajectories are queued for next week's shard.

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

**This is merge-without-gradient** — no training steps occur. The accumulated LoRA delta (30 nights of SFT + 4 DPO runs) is baked back into the frozen base weights arithmetically, then the LoRA delta is reset to zero for the next cycle. This resets accumulated rank drift and keeps the serving checkpoint lean.

```python
def monthly_merge():
    model = load_base_with_lora(prod_adapter)
    merged = model.merge_and_unload()          # arithmetic merge, no gradients
    # Quantize for serving (pick one):
    requantize(merged, target="AWQ-INT4")      # consumer GPU path
    # requantize(merged, target="NVFP4")       # DGX Spark path via TensorRT Model Optimizer
    reset_lora_delta(merged)                   # fresh LoRA delta = zero, ready for next cycle
    recompute_ewc_fisher(merged, anchor=general_anchor)  # update forgetting regularizer
    refresh_oss_instruct_seeds()               # rotate synthetic seed pool
    rotate_frontier_teacher()                  # Claude → GPT → Gemini → back to Claude
    full_eval_sweep()                          # all benchmarks + forgetting probe at new baseline
    tag = f"lamark-base-v{datetime.utcnow():%Y.%m}"
    kb.upsert_release(tag, lineage)
```

The monthly merge is the ONLY time a new base checkpoint is created. All nightly SFT and weekly DPO runs operate as LoRA deltas on top of last month's base. Never treat the monthly merge as a training run — it produces no new knowledge; it only consolidates accumulated adapters.

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
