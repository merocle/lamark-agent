# 10 — Roadmap (open questions) and non-goals

## Open questions

1. **Hardware target** — Primary training box is DGX Spark (confirmed: BF16 fits, NVFP4 serving works, Megatron-Bridge `nano-v3` runs). Dev path for 12 GB RTX: Qwen3-4B-Instruct-2507 QLoRA (8–10 GB). Dev path for Apple Silicon (M3 Pro 36 GB): Gemma4-27B via MLX-LM. **Resolved: Spark is primary; others are listed fall-backs.** Still open: when to purchase a second Spark for dual-Spark clustering (ConnectX-7 scale-out).
2. **Trace consent UX** — silent capture with off-switch, or explicit opt-in per session?
3. **First base model** — Qwen3.6-35B-A3B or Nemotron-3-Nano-30B-A3B? Recommendation: start with Nemotron (Megatron-Bridge pipeline more mature, Nemotron-Agentic-v1 schema alignment exact, KV cache advantage on Spark). Switch to Qwen3.6 if multi-adapter hot-swap is needed before monthly merge cycles. Still open: commit to one or keep both in the nightly pipeline.
4. **Frontier teacher budget** — Claude + GPT + Gemini API spend in month 1, or skip OSS-Instruct expansion?
5. **knowledge-base contract pin** — which version of `../../knowledge-base/docs/07b-public-api-rfc.md` are we coding against? (Capture a hash; coordinate on breaking changes.)
6. **Plugin host** — WASM-only (safer), dylib-only (faster), or both? (See [`../plan/08-layer-7-skills-plugins-curator.md`](../plan/08-layer-7-skills-plugins-curator.md).)
7. **Qwen3.5 DeltaNet LoRA target modules** — the Gated DeltaNet SSM layers in Qwen3.5 dense models use different projection names than standard Transformer attention. Must run `model.named_modules()` before setting `target_modules`; confirm with `print_trainable_parameters()` that both attention and SSM layers are included. Treat as experimental until Unsloth publishes a confirmed recipe.
8. **Qwen3.5 multimodal in Lamark** — the 0.8B–9B models are natively multimodal (early-fusion image+text). Lamark v0.1 is text-only. Decide: text-only inference, or extend the `ModelProvider` trait to support image inputs. Text-only is the safe v0.1 choice; multimodal can unlock vision tools in v0.2 (matches the deferred browser/vision tools in [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md)).

## Non-goals (v0.1)

- Multi-tenant SaaS. Single-user-per-process; per-project isolation via knowledge-base.
- RBAC at the runtime layer (knowledge-base owns auth).
- **GRPO/RLVR.** SFT (nightly) + DPO (weekly) only in v0.1. GRPO requires stable SFT baseline (≥ 30 clean nights) + task verifiers + NeMo Gym integration — a v0.2+ deliverable. See [`08-training-pipeline.md`](./08-training-pipeline.md) Tier 3.
- **Full weight fine-tuning on DGX Spark.** Not physically viable: 30B+ MoE optimizer states exceed 128 GB UMA. Monthly merge is arithmetic LoRA-delta baking, not gradient training.
- **CPT (Tier 0) by default.** Only activated manually for raw domain corpora > 50 MB.
- Mobile / Termux. Deferred.
- Windows native. macOS + Linux only at v0.1.
