# Lamark

> The agent that grows with you — locally.

Lamark is a personal AI agent that runs on a single NVIDIA DGX Spark and learns from accumulated dialogue over time. It is built as a fork of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (vendored at `vendor/hermes/`, MIT) with Lamark-specific additions: a redaction safety gate, a training-data archive, dual-write wiring, and a force-fine-tune CLI.

The name is a nod to Jean-Baptiste **Lamarck**: traits acquired during one "day of use" are passed to the next generation of weights through nightly LoRA training. Not Darwinian selection — Lamarckian inheritance.

## Status

**Pre-alpha, Plan A complete.** End-to-end `lamark chat` works against a live Qwen3.6-35B-A3B on Spark, returning memory-injected responses. `lamark train` is wired but the actual Unsloth dispatch is Module 14 (Phase 2 in v4 plan). 119 tests green.

## Quick start

On a fresh DGX Spark (DGX OS Ubuntu 24.04, CUDA 13.x):

```bash
git clone <this-repo> ~/lamark-agent
cd ~/lamark-agent
./scripts/setup_spark.sh              # installs torch via NGC container, llama.cpp, models
source ~/.lamark/env
python scripts/smoke_test.py \
    --output smoke_test_results/first.json
```

After smoke test passes (≥ 22 tok/s median single-stream decode on Qwen3.6-35B-A3B FP8 via vLLM):

```bash
# Day-0: seed identity + import history
lamark bootstrap --name "Anna" --locale "ru-RU" \
    --role "ML engineer at X, lives in Berlin" \
    --chatgpt ~/Downloads/chatgpt-conversations.json \
    --obsidian ~/Documents/Obsidian

# Chat (single-shot, MVP — multi-turn via Hermes loop comes via `lamark` binary)
lamark chat "что у нас на сегодня?"

# Inspect the training-data archive
lamark train --status

# Force a fine-tune (refused if archive < 50 records unless --no-threshold)
lamark train --now
```

## Architecture

```
                            ┌──────────────┐
                            │     User     │
                            └──────┬───────┘
                                   │
                ┌──────────────────▼──────────────────┐
                │  Lamark (built on vendored Hermes)  │
                │  • redaction gate (HALT-on-secret)  │
                │  • single-shot `lamark chat`        │
                │  • multi-turn `hermes`-style CLI    │
                │  • cron, skills, channels, sandbox  │
                └──────┬──────────────────────────┬───┘
                       │                          │
                ┌──────▼──────────┐    ┌──────────▼──────────┐
                │  MemoryStore    │    │  Training-data      │
                │  (Fact table)   │◄───┤  archive (JSONL)    │
                │                 │    │  + sensitivity tags │
                └─────────────────┘    └──────────┬──────────┘
                                                  │
                                       ┌──────────▼──────────┐
                                       │  `lamark train`     │
                                       │  curation pipeline  │
                                       │  → Unsloth on Spark │
                                       │  → LoRA adapter     │
                                       │  → eval-gate promo  │
                                       └─────────────────────┘
```

For the full design rationale, see `../feasibility-report-v4.md`. The
Hermes-integration trade-offs are documented in
`docs/hermes-vs-lamark-analysis.md`.

## Repository layout

```
lamark-agent/
├── src/lamark/             Python package
│   ├── memory/             Fact archive (SQLAlchemy)
│   ├── archive/            Training-data archive (JSONL)
│   ├── train/              Curation + trainer dispatch (Plan A.5)
│   ├── inference/          vLLM + llama.cpp routing
│   ├── bootstrap/          Day-0 wizard + ChatGPT/Obsidian importers
│   └── redaction/          PII + secrets pipeline (HALT-on-secret)
├── vendor/hermes/          Vendored Hermes Agent (Nous Research, MIT)
│   ├── LICENSE             Original — preserved verbatim
│   ├── UPSTREAM.md         Pinned SHA + dual-attribution
│   └── MODIFICATIONS.md    Lamark patch stack (Plans A.2 + A.3 + A.4)
├── scripts/                smoke_test.py, setup_spark.sh, vllm_server.sh
├── docker/                 Dockerfile.vllm (NGC pytorch + vLLM)
├── tests/                  pytest suite (119 tests green)
└── docs/                   Design notes, troubleshooting, smoke-test reports
```

## Attribution

Lamark is built on a vendored copy of [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research, distributed under the MIT License. The upstream code lives under `vendor/hermes/` with its original LICENSE preserved verbatim and the pinned SHA recorded in `vendor/hermes/UPSTREAM.md`. Lamark modifications to vendored Hermes files are marked `LAMARK-PATCH (A.N)` at each touched line and catalogued in `vendor/hermes/MODIFICATIONS.md`.

New Lamark code under `src/lamark/` and the LAMARK-PATCH diff stack are © 2026 Lamark contributors. Both copyright lines must travel together in any redistribution per MIT.

## License

MIT — see [LICENSE](./LICENSE) and [vendor/hermes/LICENSE](./vendor/hermes/LICENSE).
