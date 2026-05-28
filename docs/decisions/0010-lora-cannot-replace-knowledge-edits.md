# 0010. LoRA cannot replace L3 knowledge edits — empirical validation

**Status:** accepted
**Date:** 2026-05-28

## Context

The SPEC.md `What it does` table splits "knowing" into four mechanisms:

| Layer | Stores | Mechanism |
|---|---|---|
| L1 — Identity | "I am Lamark" | `chat_template.jinja` |
| L2 — Episodic memory | "User likes blue" | Hermes memory tool + RAG |
| L3 — Knowledge edits | High-priority facts as weights | **ROME-style rank-1 surgery (planned)** |
| L4 — Style / voice | How you write | **LoRA fine-tuning** |

The "honest Lamarckian claim" depends on L3 and L4 both modifying model parameters. The split between them — knowledge editing vs LoRA — was designed but not yet validated empirically. This ADR records the validation experiment.

## Experiment

Trained Nemotron-3-Nano-4B-BF16 LoRA adapters on a synthetic Q&A dataset about the Lamark project itself (1805 examples generated via `gpt-5.4-mini`, 18 topic clusters, including an explicit "identity_disambiguation" topic with 100 examples contrasting "Lamark is X, not Y").

Training: `bf16` LoRA, r=16, α=32, target modules `q/k/v/o_proj + gate/up/down_proj`, lr=1e-4 cosine, 600 steps over 1625 train examples (~3 epochs), `apply_chat_template` with `enable_thinking=False`, on a DGX Spark via the `learning/scripts/spark/` pipeline.

Validation: `eval_loss = 1.879` (PPL ≈ 6.55); per-token PPL on 50 val samples dropped from base 7801 to adapter 205 (38× improvement on the training distribution).

Interactive evaluation via vLLM (`vllm/vllm-openai:v0.21.0`, OpenAI-compatible API, `temperature=0`, `enable_thinking=False`) — 8 Lamark-specific questions, base vs adapter side by side.

## Result

| Question type | Adapter behavior |
|---|---|
| Specific Lamark vocabulary ("Lamark runtime", "lamark crate") | Adapter learned (2/8 questions) |
| Niche facts (4 memory layers, trace bundle structure, Curator's 7-day cycle, Kreuzhofer OOM) | Adapter did NOT override base priors |
| Ambiguous wording ("What is Lamark?", "memory layers") | Adapter defaulted to Lamarck-the-biologist despite 100 explicit disambiguation examples |
| Strong wrong priors ("Curator agent" → LangChain; "knowledge-base service" → enterprise AI) | Adapter could not override |

Net: ~2/8 hits, both at the level of *vocabulary* not *facts*. The 38× PPL improvement on the training distribution did not translate to overriding deep model associations.

## Decision

The empirical result confirms the existing architectural decision: **L3 (knowledge edits) cannot be implemented as LoRA fine-tuning**. LoRA shifts style, format, and vocabulary; it does not override strongly-anchored factual associations in the base model.

L3 must be implemented as a separate mechanism — ROME-style rank-1 weight surgery, MEMIT for batch edits, or a similar targeted approach — exactly as the spec already specifies.

## Consequences

1. The `learning/` pipeline as-is is **correct for L4** (style/voice from user turns) and should not be repurposed for L3.
2. L3 implementation needs its own design pass. Reference targets: ROME (Meng et al. 2022), MEMIT (Meng et al. 2023). The training pipeline boundary stays the same (file-system handoff + KB sync), but the editor itself is a different process.
3. Synthetic project-knowledge datasets (`learning/scripts/generate_lamark_dataset.py`) are useful as **L4 style anchors** (the model will at least produce Lamark-flavored vocabulary) but should not be relied on for factual recall.
4. The Spec text in `SPEC.md §What it does` is reinforced, not changed — the table was already correct.

## Notes

- Dataset, scripts, and the vLLM probe (`learning/scripts/spark/probe_vllm.sh`) are committed and reusable for future experiments.
- The decode loop bug for NemotronH (`model.generate()` requires `NemotronHHybridDynamicCache` initialisation that `transformers` does not provide externally; manual greedy decode produces SSM-state-frozen garbage) is sidestepped by vLLM, which is the production-correct path anyway.
