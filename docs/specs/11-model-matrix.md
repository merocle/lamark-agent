# 11 — Target LLM matrix

**Qwen3.5 family** (Qwen Team, Feb 2026; Apache 2.0; citation arXiv:2026/qwen3.5 "Towards Native Multimodal Agents"):

Qwen3.5 is **not** Qwen3 + increment. It is a new architecture family:
- **Hybrid Gated DeltaNet + Gated Attention** — SSM layers replace some attention layers (similar concept to Nemotron's Mamba-2 hybrid). Linear-complexity SSM layers have fixed recurrent state (no KV-cache growth), giving context efficiency advantages.
- **Native multimodal** — early-fusion image+text pre-training from the start.
- **262K native context, extensible to 1.01M** — across all sizes.
- **201 languages** and RL training across million-agent environments.
- `Qwen3.6-35B-A3B` (below) uses the `qwen3_5_moe` architecture ID — it is the **MoE branch** of this same family. The 0.8B–9B are the **dense branch**.

> **LoRA target modules for DeltaNet layers differ from pure-Transformer.** Unsloth support for the `qwen3_5` architecture is evolving — verify `FastModel.from_pretrained` accepts the model before committing to a training run. See [`../plan/10-training-pipeline.md`](../plan/10-training-pipeline.md) §Tier 1.

| Model | Params | Layers | Context | Autonomous agent? | Rec. quant | Peak VRAM | Train via |
|---|---|---|---|---|---|---|---|
| **Qwen3.5-0.8B-Base** | 0.8B dense | 24 | 262K (1M ext.) | **No** — classifier/extractor only; wrap with deterministic logic; **MTP-trained** | Q8_0 or BF16 | 128K+ | Unsloth QLoRA r=16; ~2–3 GB |
| **Qwen3.5-2B-Base** | 2B dense | 24 | 262K (1M ext.) | Borderline — single narrow domain, single-shot tool calls, weak beyond ~5 turns | Q6_K or Q8_0 | 128K | Unsloth QLoRA r=16–32; ~4–5 GB |
| **Qwen3.5-4B-Base** | 4B dense | 32 | 262K (1M ext.) | **Yes** — practical floor for autonomous multi-turn agents | UD-Q5_K_XL or Q6_K | 64K+ | Unsloth QLoRA r=32; ~7–9 GB |
| **Qwen3.5-9B-Base** | 9B dense | 32 | 262K (1M ext.) | **Yes** — full-capability agent; stronger than Qwen3-8B at same size tier | UD-Q4_K_XL or Q5_K_M | 32K (FP16-KV) / 64K (q8_0 KV) | Unsloth QLoRA r=32; ~11 GB |

> **Deploy:** all four via `llama-server --jinja` (llama.cpp sm_121 build for Spark). Never vLLM on 12 GB. Serving params: `--temp 0.7 --top-p 0.8 --top-k 20` (tool loops). **Never greedy** — Qwen3.5 inherits the Qwen3 greedy-loop bug.

> **0.8B / 2B are not autonomous agents.** Deploy inside a deterministic harness. Using them as agentic loops produces procedural-failure modes dominant in sub-4B models (arXiv:2601.16280).

**Large / MoE models:**

| Model | Params | Context | Notes | Train via |
|---|---|---|---|---|
| **Qwen3.6-35B-A3B** (`qwen3_5_moe` arch — MoE branch of Qwen3.5 family) | 35B MoE, 3B active | 32K | Strong tool-calling baseline. Multi-adapter vLLM (`--enable-mixed-moe-lora-format`). Never greedy. | Unsloth bf16 LoRA on Spark; kreuzhofer eager-load patch. |
| **NVIDIA-Nemotron-3-Nano-30B-A3B-BF16** | 31.6B total, ~3B active | 1M | Mamba-2/Transformer hybrid (23 MoE+23 Mamba-2+6 GQA, NoPE). KV 3× smaller than pure-Transformer MoE. Schema-aligned with `Nemotron-Agentic-v1`. NVFP4 prod: 65 tok/s / 167 tok/s @10 concurrent. | Megatron-Bridge `nano-v3` branch (~5–6 h on Spark). |
| **Gemma4-27B** | 27B dense | — | Apple-Silicon flagship (M3 Pro 36 GB). MLX-LM. | Unsloth LoRA + MLX. |
| **NVIDIA-Nemotron-3-Super-120B-A12B** | 120B total, 12B active | — | NVFP4-pretrained; MTP (3× structured-gen speedup); Latent MoE. Teacher model / hosted endpoint only — not fine-tunable on single Spark. | Multi-node H100/B200 only. |

Model choice is config, not code. One Rust provider implementation (`LocalOpenAICompat`) talks to vLLM / Ollama / llama.cpp / SGLang / LM Studio. Gemma4 (27B dense) is the Apple-Silicon flagship path via MLX-LM. A second (`AnthropicCompat`) carries cache_control breakpoints. The `ModelProvider` trait keeps these isolated.

> **Serving reference commands** (vLLM/llama.cpp/multi-adapter) live in
> [`../plan/10-training-pipeline.md`](../plan/10-training-pipeline.md) §Serving.
