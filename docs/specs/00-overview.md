# 00 — Overview

> Self-improving local agent. Rust runtime (`agent/`), Python training pipeline (`learning/`), knowledge-base service (`../../knowledge-base`) as the system of record.

**Status:** Draft v0.4 — 2026-05-29 (split from the former monolithic spec).

> Specs are the *what/why*. Implementation plans (the *how*) live in
> [`../plan/`](../plan/); complete system-flow diagrams in
> [`../flow.md`](../flow.md).

---

## Mission

Build an open-source **Rust** agent that:

1. **Runs locally** on commodity hardware against an open-weights LLM (Qwen3.5-9B, Qwen3.6-35B-A3B, Gemma4-27B, or Nemotron-3-Nano), and against any OpenAI-compatible endpoint.
2. **Captures every interaction** — system prompt, user input, model reasoning, tool call, tool result, approval decision, error, gateway event — into a structured, replayable trace bundle.
3. **Stores all of it in `knowledge-base`** — every trace, every reduced conversation, every promoted-or-rejected adapter, every gold/probe sample — one canonical source of truth for data; Lamark owns the runtime and the training schedule.
4. **Closes the loop via a five-tier adaptation stack:**
   - **Harness** (LIFE-HARNESS, arXiv:2605.22166) — fixes 90% of failures (interface, not reasoning) at zero weight cost; weekly evolution from traces.
   - **Skills** (MUSE, arXiv:2605.27366 + SkillOpt, arXiv:2605.23904) — created on-demand from experience, optimized weekly; +23 pp documented with no weight changes.
   - **SFT LoRA** — residual reasoning failures only (~10%); nightly; anti-forgetting guards + forgetting-probe diagnostics.
   - **DPO** — alignment polish; weekly.
   - **GRPO/RLVR** (v0.2+) — verifier-gated RL after SFT is stable.
5. **Stays explainable**: every training sample traces back to the live session by `rollout_id`; every promoted adapter traces back to the data + eval that approved it. Every skill edit is validation-gated and auditable.

---

## Reference inventory

| Source | Path | Role |
|---|---|---|
| NousResearch/hermes-agent (cloned) | `~/.cache/lamark/vendor/hermes-agent` | Architecture model; we re-implement in Rust. v0.14.0 "Foundation Release" (May 2026). Deep-dive in [`../plan/00c-hermes-deepdive-addendum.md`](../plan/00c-hermes-deepdive-addendum.md). |
| gitverse claude-code mirror (cloned) | `~/.cache/lamark/vendor/claude-code` | Hooks + prompt composition design reference. |
| openai/codex (cloned) | `~/.cache/lamark/vendor/codex` | Rust source we read directly; trace + provider trait + sandbox patterns. |
| `../../knowledge-base` | local repo | Canonical memory + dataset + eval-set store; Kotlin/Spring. |

---

## Spec index

| File | Topic |
|---|---|
| [`01-scope-and-inheritance.md`](./01-scope-and-inheritance.md) | What we inherit from Hermes / Claude Code / Codex; Rust-from-day-1. |
| [`02-knowledge-base-integration.md`](./02-knowledge-base-integration.md) | knowledge-base as system of record; data → endpoint map. |
| [`03-architecture.md`](./03-architecture.md) | High-level architecture; three processes, three lifecycles. |
| [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md) | Tool-call protocol + full tool catalog (Hermes base + Lamark). |
| [`05-runtime-layers.md`](./05-runtime-layers.md) | The eight Rust runtime layers (index into `../plan/`). |
| [`06-trace-and-data-format.md`](./06-trace-and-data-format.md) | Trace bundle (training-data format). |
| [`07-configuration.md`](./07-configuration.md) | `~/.lamark/config.yaml` contract. |
| [`08-training-pipeline.md`](./08-training-pipeline.md) | Training policy: blend, tiers, eval gates (ops in `../plan/10`). |
| [`09-security-posture.md`](./09-security-posture.md) | Sandbox tiers + permission-first policy. |
| [`10-roadmap-and-non-goals.md`](./10-roadmap-and-non-goals.md) | Open questions + v0.1 non-goals. |
| [`11-model-matrix.md`](./11-model-matrix.md) | Target LLM matrix (Qwen3.5 family, MoE, serving). |

## Plan suite (the *how*)

See [`../plan/00-overview.md`](../plan/00-overview.md) for the full, ordered plan
index (phase ordering, definition of done) and the layer-by-layer implementation
plans referenced from [`05-runtime-layers.md`](./05-runtime-layers.md).
