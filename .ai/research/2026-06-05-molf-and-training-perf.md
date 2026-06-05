# MoLF-E, training-perf optimization, and adapter comparison

**Date:** 2026-06-05
**Author:** session report (Claude Opus 4.8)
**TL;DR:** Implemented MoLF-E from arXiv:2605.07111 (no code released). Optimized
training ~3.4× (packing + flash-linear-attention). Benchmarked LoRA vs MoLF-E:
**tuned MoLF-E v2 (r=128/256) scores a perfect 100% gate** (identity / knowledge /
tools / regression) and is the best adapter; plain LoRA already suffices for the
current ~70-fact knowledge scale (full FT is unnecessary and OOMs the 9B on Spark).

Continues [`2026-05-29-sft-identity-knowledge-loop.md`](./2026-05-29-sft-identity-knowledge-loop.md).

---

## 1. Training-performance optimization

Analysis: a 9B LoRA used only **~26 GB of ~118 GB** at `batch=1` (90 GB idle).

| Change | Effect |
|---|---|
| Naive batch-up (1→8, no packing) | **regressed** to 28 s/step — short Q&A padded to batch max (~13× wasted tokens) |
| **Packing** (concat short rows → dense `max_length`) | 2465→213 rows, 310→28 steps, ~57 min, zero padding |
| **flash-linear-attention** (fast Qwen3.5 gated-DeltaNet kernels) | killed the "fast path not available" torch fallback; 127→62 s/step (~2×) |
| Combined (batch 8 + packing + fla) | **~83 min → ~24 min (~3.4×)**, peak 72 GB |

Key insight: wall-clock is **token-bound** (the DeltaNet kernel), not memory-bound —
bigger batches raise memory + cut step count but not total time. The real speed
lever was `fla`. Scripts gained env knobs: `BATCH_SIZE/GRAD_ACCUM/GRAD_CKPT/PACK/DL_WORKERS/EPOCHS/LR`.

## 2. MoLF-E implementation (arXiv:2605.07111, from scratch)

No code was released, so implemented in `learning/scripts/spark/molf.py`:
- **MoLFLinear** — frozen base + K LoRA experts of different ranks, RS-LoRA
  superposition (all experts active each forward).
- **SparseAdamW** — universal Adam-moment tracking every step; per module per step,
  only the Top-1 expert by the Expected-Preconditioned-Descent score
  `S = (lr/N)·Σ m²/(√v+ε)` gets a physical weight update.
- **Fold-to-LoRA export** — concatenate experts into one standard rank-(Σrᵢ) PEFT
  adapter (scaling baked in, alpha=r), so serving/probing are unchanged.
- `train_molf.py` wires it via HF `Trainer` (custom optimizer), reusing the agentic
  dataset (prose + tool trajectories, packed).

Validated: injection (128 modules × 2 experts), routing runs, loss drops, export →
PEFT load clean (`sum|lora_B|` nonzero, no key warnings). **Full MoLF** (FFT expert)
is infeasible on the 9B (~144 GB optimizer state → OOM); only **MoLF-E** fits.

Bug fixed: must freeze the **entire** base before injection (else ~4 B params keep
`requires_grad` and waste memory/compute, though the optimizer still trains only
experts). After fix: 349 M trainable experts (r=64/128), 698 M (r=128/256).

## 3. Adapter comparison (scored `probe_gate.py`)

| Adapter | Method | Identity | Knowledge | Tools | Regression | Native tool-calls |
|---|---|---|---|---|---|---|
| `qwen3_5-9b-instruct-tools-lora` | LoRA r=32, 2ep | 100% | 100% | 75% | 100% | no (prose-biased) |
| `qwen3_5-9b-agentic-lora` | LoRA + trajectories, 2ep packed | ✗ regressed* | — | — | — | yes |
| `qwen3_5-9b-molf-lora` | MoLF-E r=64/128, 4ep | 100% | 75% | 75% | 100% | yes |
| **`qwen3_5-9b-molf-lora-v2`** | **MoLF-E r=128/256, α=32, 4ep** | **100%** | **100%** | **100%** | **100%** | **yes** |

\* the agentic LoRA regressed identity/knowledge purely from **undertraining** —
packing cut it to 28 optimizer steps (vs 290 for tools-lora). Not a LoRA capacity limit.

**Winner: MoLF-E v2** — perfect across all buckets + native tool-calls in one model.
Higher expert capacity (r=128/256) closed v1's knowledge 75→100 and tools 75→100.

## 4. Conclusions

- **LoRA is sufficient for the current knowledge scale** (~70 facts + identity):
  plain r=32 LoRA already hit knowledge 100%. The earlier ADR-0010 "LoRA can't hold
  facts" was at 4B and about *overriding deep priors*; on the 9B instruct, with
  enough steps, it holds.
- **Full fine-tuning is unnecessary here and infeasible on Spark** (9B FFT ≈ 144 GB
  with AdamW > 118 GB). MoLF-E gives the LoRA↔FFT spectrum at LoRA memory cost.
- **Capacity *does* help at the margin:** only the higher-rank MoLF-E reached tools
  100% (r=32 LoRA capped at 75%) — partial vindication of the "more capacity"
  instinct, achieved via expert rank, not full FT.
- **The raw instruct Qwen3.5-9B already emits native tool calls;** prose-only SFT
  degrades that, trajectory training restores it. Don't over-prose-train.

## 5. Known gaps / follow-ups

- **`assistant_only_loss` is a no-op on Qwen3.5** (template lacks `{% generation %}`)
  → all training is full-sequence. Fix: response-template collator. Affects every trainer.
- "What tools can you use?" phrasing coverage was thin (fixed at higher capacity, but
  add more list-style examples for robustness).
- Probe artifact: with free generation the model spills past `<|im_end|>` into a
  hallucinated next turn — cosmetic (the runtime stops at turn boundary).

## 6. Artifacts

- Code: `learning/scripts/spark/{molf.py,train_molf.py,train_sft_agentic.py,train_sft.py,probe_gate.py,probe_compare.py,probe_trajectory.py}`, `learning/docker/Dockerfile.sft` (+fla).
- Adapters (Spark `~/.lamark/checkpoints/`): `qwen3_5-9b-molf-lora-v2` (best), `-molf-lora`, `-agentic-lora`, `-instruct-tools-lora`.
- Commits: `ca50e0c` (packing), `eb69701` (fla), `be7bb08` (MoLF-E), `8e15532` (docs).
