# Lamark SFT identity + knowledge training loop

**Date:** 2026-05-29
**Author:** session report (Claude Opus 4.8)
**Outcome:** Working, repeatable SFT-LoRA loop that injects Lamark knowledge **and**
identity into `Qwen/Qwen3.5-9B` (instruct). Scored gate PASS. MEMIT path abandoned.

---

## 1. Objective

Train a model with Lamark's knowledge and identity, and establish a loop to keep
updating it. The session started aimed at MEMIT knowledge-editing (the then-current
"L3" plan) on `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`, pivoted twice, and
landed on a data-driven SFT-LoRA loop.

---

## 2. MEMIT dead-end (NemotronH-30B-A3B)

Attempted batch MEMIT via EasyEdit. Five runs, each surfacing one layer of breakage:

| # | Failure | Fix attempted |
|---|---|---|
| 1 | `ModuleNotFoundError: transformers` | Built `lamark/edit:26.01` (EasyEdit dep surface, torch 2.10 preserved) |
| 2 | `higher`/`omegaconf`/`timm`/`cv2`/`peft` missing (EasyEdit eager-imports its multimodal editors) | Added all; validated import chain offline |
| 3 | `MEMITHyperParams.__init__() missing 24 args` | Rewrote `_build_easyedit_hparams` to pass all dataclass fields as kwargs |
| 4 | `LookupError: model.layers.20.mlp.experts.0.down_proj.weight` | Discovered real arch from safetensors index; switched to shared-expert target |
| 5 | `AttributeError: 'NemotronHCausalLMOutput' has no 'past_key_values'` | **Fatal** — EasyEdit `generate_fast` needs a standard KV cache |

**Verified NemotronH architecture** (from `model.safetensors.index.json`, no model load):
52 layers, prefix `backbone.layers.{i}`, alternating **Mamba / MoE / Attention**.
MoE layers have 128 routed experts (`mixer.experts.{0..127}`) + an always-active
`mixer.shared_experts`. Mid-stack MoE = `[20,22,24,27,29]`.

**Why it's a dead-end:** EasyEdit's context-template generation (`get_context_templates`
→ `generate_fast`) does manual KV-cache greedy decode and reads `output.past_key_values`,
which NemotronH's hybrid Mamba cache does not expose. This is the same non-standard-decode
issue noted in the 2026-05-28 experiment. Not patchable without rewriting EasyEdit's
decode path. **Do not reattempt MEMIT on hybrid-architecture models.**

---

## 3. Pivot 1 — to the plan's SFT-LoRA tier

`docs/plan/10-training-pipeline.md` (reworked this day) has **no knowledge-editing
path**. Canonical method is **Tier-1 SFT LoRA**: bf16, r=32/α=32, `assistant_only_loss`,
cosine lr=1e-4, 1–2 epochs. Dropping EasyEdit also removes its transformers-4.57 pin,
freeing us to use current transformers.

## 4. Pivot 2 — model selection for Qwen3.5

`Qwen/Qwen3.5-9B-Base` was requested. Investigation (`config.json` + safetensors index):
- **Multimodal** (`Qwen3_5ForConditionalGeneration`, `vision_config`, LLM nested at
  `model.language_model.layers`), **hybrid attention** (32 text layers = 24
  `linear_attention` + 8 `full_attention`).
- Needs **transformers 5.x** to load (`qwen3_5` absent from 4.57.1; no remote code).
- Confirmed: transformers 5.9 maps `qwen3_5 → Qwen3_5ForCausalLM` under
  `AutoModelForCausalLM` — loads the **text-only** LM (no vision tower). So plain SFT
  LoRA works; the MEMIT-era transformers conflict is irrelevant once EasyEdit is gone.

---

## 5. SFT pipeline (the deliverable)

Image `lamark/sft:26.01` (`learning/docker/Dockerfile.sft`): NGC pytorch:26.01 +
transformers 5.9 + TRL 1.5 + peft 0.19 + torchao 0.17 + causal-conv1d.

Scripts (`learning/scripts/`):
- `build_dataset.py` — **source of truth.** IDENTITY Q&A (banks, oversampled) +
  KNOWLEDGE (`lamark_facts.jsonl` → Q&A) + GENERAL pool → `train/val.jsonl`.
- `spark/train_sft.py` — TRL `SFTTrainer`, `AutoModelForCausalLM`, bf16 LoRA,
  `assistant_only_loss`, env-driven.
- `spark/06_train_sft.sh` — Spark-side runner.
- `spark/probe_gate.py` — scored gate (identity / knowledge / regression), PASS/FAIL exit.
- `spark/probe_sft.py` — base-vs-LoRA generation comparison.

---

## 6. Results

### Run A — base model (`Qwen3.5-9B-Base`), prior 1625-sample set
r=32/α=32, 2 epochs, 204 steps, **eval_loss 0.79**. Probe: **knowledge stuck**
("written in Rust", "stores data in knowledge-base"), general intact ("Paris"), but
**identity failed** — "Who are you?" → *"I am Claude"*.

**Root cause (measured):** the 1625-sample set had **0 identity questions** and only 5
assistant turns mentioning Lamark. The model was never taught self-identity. Base models
are also a weak identity substrate.

### Run B — instruct model (`Qwen/Qwen3.5-9B`) + identity-rich dataset
`build_dataset.py` → **260 identity + 133 knowledge + 1805 general = 2198** (2067/131).
Identity enforced **by data only** (no chat-template edit, per decision). r=32/α=32,
2 epochs, 260 steps, **eval_loss 0.72**, token-acc 0.82.

**Scored gate: PASS**

| Bucket | Score | Notes |
|---|---|---|
| Identity | 83% (really 6/6) | Self-IDs as Lamark; refuses Claude/GPT. The one "MISS" is a scorer false-negative (answer echoes "ChatGPT" while refusing it). |
| Knowledge | 100% | Rust, knowledge-base storage, memory architecture |
| Regression | 100% | Paris, 2+2=4, "buenos días" — no forgetting |

Switching base→instruct and adding 260 identity samples fixed the identity gap.

---

## 7. The iterate loop

The dataset is the source of truth; updates are always the same three steps:

```
edit learning/data/lamark_facts.jsonl (knowledge) or identity banks in build_dataset.py
  → build_dataset.py   (rebuild train/val)
  → train_sft.py       (LoRA on Qwen/Qwen3.5-9B)
  → probe_gate.py      (keep adapter iff PASS)
```

Adapter: `~/.lamark/checkpoints/qwen3_5-9b-instruct-lora/`. Preserved general pool:
`~/.lamark/data/general_base.jsonl`.

---

## 8. Follow-ups (quality, not correctness)

1. **Thinking-mode leakage** — instruct Qwen3.5 prepends "Thinking Process: …".
   Serve/probe with `enable_thinking=False` in `apply_chat_template`.
2. **Gate scorer** — only flag a competitor when the model *affirms* being it, not when
   a refusal echoes the name.
3. **Cleanup** — abandoned MEMIT artifacts (`05_run_edit.sh`, `src/lamark/knowledge_edit/`,
   `Dockerfile.edit`, `configs/memit/`) remain as exploration record; remove if desired.

---

## 9. Artifacts & commits

- Image: `lamark/sft:26.01` (Spark). Models: `~/.lamark/models/hf/Qwen_Qwen3.5-9B{,-Base}/`.
- Adapters: `qwen3_5-9b-lora` (base run), `qwen3_5-9b-instruct-lora` (final).
- Commits: `399c0e0`, `439cc02` (pipeline + docs), `ef78f7f` (build_dataset.py + probe_gate.py).
- Decision record: this report supersedes the MEMIT-as-L3 mechanism; see also
  ADR-0010 and `2026-05-28-lamark-lora-experiment.md`.
