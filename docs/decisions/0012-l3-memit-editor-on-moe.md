# 0012. L3 knowledge edits are implemented via ROME/MEMIT on a MoE base

**Status:** accepted (initial implementation; hyperparameters research-grade)
**Date:** 2026-05-28
**Related:** [ADR-0010](./0010-lora-cannot-replace-knowledge-edits.md),
[ADR-0011](./0011-l1-default-chat-template.md)

## Context

ADR-0010 falsified the hypothesis that LoRA could be used as L3
(knowledge edits). The SPEC table calls for "ROME-style rank-1 surgery";
this ADR records the first concrete implementation and the unsolved
questions that fall out of running it against an MoE base.

## Decision

L3 is implemented as **batch MEMIT** (with ROME available as a fallback
for single-fact debug) targeting `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`.

The pipeline lives under `learning/`:

```
learning/
├── data/lamark_facts.jsonl               # the curated facts dataset
├── configs/memit/nemotron-h-30b-a3b.yaml # hyperparameters (research-grade)
├── src/lamark/knowledge_edit/
│   ├── facts.py                          # loader + ROME subject-in-prompt validator
│   ├── edit_runner.py                    # CLI: load model, apply edits, save
│   └── evaluator.py                      # vLLM-side probe + scoring
└── scripts/
    ├── spark/05_run_edit.sh              # on-host docker wrapper
    └── run_spark_edit.sh                 # local SSH orchestrator
```

Edited weights are written to a fresh HF model directory at
`~/.lamark/models/edited/<base-slug>__<edit-tag>/` and served via the
existing `serve_vllm.sh` (set `MODEL_DIR` to the edited dir,
`ADAPTER_DIR=''` to disable LoRA). The L1 chat template
(ADR-0011) is auto-installed into the edited dir so the edited model
ships with the Lamark identity by default.

## Why MEMIT, not LoRA, not full fine-tuning

| Method | Why rejected |
|---|---|
| LoRA SFT | Falsified empirically — see ADR-0010. Cannot override deep priors. |
| Full fine-tune (SFT/DPO) | 30B × ~3 epochs of self-knowledge data on Spark is ~weeks; risk of catastrophic forgetting; reversibility is poor. |
| **MEMIT (rank-1, batched)** | Editing one MLP matrix's row is the minimum-blast-radius way to change a factual association. Surgical, fast (~5–10 min per fact batch), reversible (re-run install with original weights). ROME is the single-fact special case, kept for debug. |

## Two unsolved questions (research-grade hyperparameters)

NemotronH-30B-A3B is **not in EasyEdit's supported model list**. Two
architectural facts mean the hparams in
`configs/memit/nemotron-h-30b-a3b.yaml` are starting points, not optima:

### Q1. Which layers are MLP-bearing?

NemotronH is hybrid Mamba-SSM / MoE-MLP / attention. Only the MoE-MLP
layers have a `down_proj` for ROME to rewrite. The runner currently
trusts the operator's `layers:` list; the YAML defaults to layers
[20, 21, 22, 23, 24] based on dense-transformer intuition. **First-run
action: dump `model.named_parameters()` and identify which of those
layers actually has a `model.layers.<i>.mlp.experts.<e>.down_proj`
weight. Update the YAML if any are SSM or attention layers.**

### Q2. Which MoE expert(s) carry the fact?

ROME and MEMIT both edit a single `down_proj` matrix. In an MoE-MLP
block there are N experts gated by a router; an edit to expert 0 only
takes effect when the router routes the target subject through expert 0.

The runner supports two modes:

- `moe_all_experts: false` (default): edit one configurable expert
  (`moe_expert_index: 0`). Cheap; routing-fragile.
- `moe_all_experts: true`: loop the edit over every expert in the chosen
  layers. Routing-agnostic but ~N× the compute. (For Nemotron-A3B, N=64
  per layer; multiply that by `layers` count and per-edit cost.)

**First-run action: try both flags on the same small fact subset and
diff the eval reports. Pick the cheapest mode that passes the
neighborhood probes.**

## Evaluation contract

The evaluator in `evaluator.py` hits a vLLM endpoint and produces a JSON
report per run. Two top-level scores:

- `edit_score`: fraction of (edit prompt + paraphrases) where the model
  reply contains the target. > 0.8 is good.
- `neighborhood_score`: fraction of neighborhood probes where the
  model's reply still contains the expected unrelated-fact substring.
  < 1.0 = unintended forgetting. Stop and reduce `mom2_update_weight`.

Run twice — base model and edited model — and diff the reports. The
expected pattern is edit_score going up significantly while
neighborhood_score stays near 1.0.

## Consequences

1. The Lamark project now has a way to teach the model facts that
   survive in the model weights, not just in retrieval (L2) or the
   system prompt (L1). This is the "honest Lamarckian" claim.
2. Edited model dirs are large (30B BF16 ≈ 60 GB). Storage on Spark is
   the binding constraint; `~/.lamark/models/edited/` is a candidate
   for periodic cleanup of stale edit tags.
3. The hyperparameter sweep above is the next session's first work. It
   needs to be done by hand on Spark with the editor's `--dry-run` mode
   plus a small subset of facts; do not run the full edit-and-serve
   loop blindly.
4. Once `layers` and `moe_all_experts` are settled, append the chosen
   values to `learning/configs/memit/nemotron-h-30b-a3b.yaml` and
   summarise the empirical result in `.ai/research/` (matches the
   convention from [project-research-in-dot-ai] memory).

## Notes

EasyEdit's stock `apply_memit_to_model` is the entry point used. We pass
in a per-expert `rewrite_module_tmp` and let it run unchanged. If the
covariance-matrix estimation (`mom2_*`) fails to load Wikipedia
statistics for NemotronH's vocabulary, the fallback is to compute
covariance from a small in-house corpus (open issue, not blocking the
first edit run).
