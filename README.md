# Lamark

> The agent that grows with you — locally.

Lamark is a personal AI agent that runs on a single NVIDIA DGX Spark and learns from accumulated dialogue over time. It is a fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research, under the MIT License.

The name is a nod to Jean-Baptiste **Lamarck**: traits acquired during one "day of use" are passed to the next generation of weights through nightly LoRA training. Not Darwinian selection — Lamarckian inheritance.

## Status

**Pre-alpha.** Phase 0 (smoke-test + provisioning). Architecture is locked per `../feasibility-report-v3.md`. Phase 1 (fork + memory + bootstrap) starts after Phase 0 smoke-test passes on real Spark hardware.

## Quick start

On a fresh DGX Spark (DGX OS Ubuntu 24.04, CUDA 13.x):

```bash
git clone <this-repo> ~/lamark-agent
cd ~/lamark-agent
./scripts/setup_spark.sh              # installs vLLM, llama.cpp, Unsloth, models
source ~/.lamark/env
python scripts/smoke_test.py \
    --output smoke_test_results/first.json
```

**Hard gate:** the smoke test must report `PASS` (≥ 25 tok/s median single-stream decode on `Qwen/Qwen3.6-35B-A3B` FP8 via vLLM) before any Phase 1 work begins. If it `FAIL`s (< 22 tok/s), see `docs/troubleshooting.md`.

## Architecture (one screen)

```
Hermes Agent fork (lamark CLI / 20+ channel gateway)
├── Memory: Honcho user model + cross-session recall + skill library
│   └── Vector store: LanceDB (embedded)  | Embeddings: bge-m3
├── Inference router (small Qwen 0.6B classifier)
│   ├── Qwen3.6-35B-A3B FP8 via vLLM   ←  ~28-30 tok/s, default
│   └── Qwen3.6-27B Q4   via llama.cpp ←  ~12-15 tok/s, style-critical (Phase 2)
└── Fine-tune loop (Phase 2, weeks 6+):
    ├── Style LoRA on dense 27B (Panza pattern, Unsloth)
    └── Procedural ESFT on MoE (two-adapter, O-LoRA orthogonality)
```

For the full feasibility analysis and design rationale, see `../feasibility-report-v3.md`.

## Why this stack

- **MoE primary** for chat speed (5× faster than dense on bandwidth-bound DGX Spark).
- **Dense secondary** for style LoRA (Panza pattern proven on dense; MoE expert routing fights style localization).
- **Hermes native memory** (no Letta/Mem0 on top — single source of truth).
- **LanceDB embedded** (no Qdrant server overhead for single-user).
- **Day-0 bootstrap wizard** (ChatGPT/Notes/Obsidian import) — closes the cold-start gap that other memory-layer assistants suffer through weeks 1-3.

## Repository layout

```
lamark-agent/
├── src/lamark/              Python package (Phase 1+)
│   ├── agent/              Hermes-derived agent loop
│   ├── memory/             Honcho user model + LanceDB integration
│   ├── inference/          vLLM + llama.cpp routing
│   ├── bootstrap/          Day-0 onboarding wizard
│   ├── fine_tune/          Phase 2: Unsloth + ESFT + style LoRA
│   ├── redaction/          PII / secrets pipeline for training data
│   └── eval/               Regression gates for LoRA promotion
├── scripts/                Operational scripts
│   ├── smoke_test.py       Phase 0 go/no-go gate
│   └── setup_spark.sh      One-shot Spark provisioning
├── tests/                  pytest test suite
├── configs/                YAML configs (models, training, memory)
├── adapters/               Trained LoRA adapters (gitignored)
└── docs/                   Design notes, troubleshooting
```

## Attribution

Lamark is a fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research, under the MIT License. See `LICENSE` for the full notice. Where Hermes Agent code is preserved we leave Nous Research's copyright in place; new code is © Lamark contributors.

## License

MIT — see [LICENSE](./LICENSE).
