# AGENTS.md — guidance for AI coding agents working on this repo

This file follows the [AGENTS.md convention](https://agents.md) (OpenAI / Linux Foundation Agentic AI Foundation, 2025). It describes how AI agents (Claude Code, Cursor, Cline, Aider, Goose, Hermes Agent itself, etc.) should approach work in this repository.

## Project context (1-paragraph)

Lamark is a personal AI agent running on a single NVIDIA DGX Spark. Forked from Hermes Agent (Nous Research, MIT). Uses Qwen3.6-35B-A3B (MoE) for default chat and Qwen3.6-27B (dense) as a fine-tune target for style LoRA in Phase 2. Memory layer is Hermes-native (Honcho user model + cross-session recall + skills) backed by LanceDB. The architecture is locked in `../feasibility-report-v3.md`; substantive deviations require a v4 spec round.

## Hard rules

1. **Never delete or modify the original `LICENSE`**. Nous Research's MIT copyright is mandatory. Add new copyright lines above, never replace.
2. **Never call the product anything other than Lamark** in user-facing strings. The upstream "Hermes" name appears only in `LICENSE` and `README.md` attribution.
3. **Never bake real user data into git**. Adapters, memory store, curated training pairs, and `.env` are gitignored — keep it that way. Verify with `git status` before commit.
4. **Never use bitsandbytes QLoRA for MoE training on DGX Spark.** Confirmed OOM-at-load at 4% (Kreuzhofer, NVIDIA forum). Use bf16 LoRA with the eager-loader patch instead.
5. **Never enable DeepSpeed ZeRO-3 for LoRA training on Qwen3-VL / Qwen3.6 MoE.** Breaks gradients (laurentdangela, NVIDIA forum). Use single-device or ZeRO-2.
6. **Never unfreeze the MoE router** during LoRA training. Unsloth disables it by default and ESFT freezes it for a reason — pre-trained routing is load-bearing.

## How to plan changes

- Skim `../feasibility-report-v3.md` (canonical architecture spec) and the per-phase task list (`docs/phase-1-plan.md` once it exists) before starting.
- Stay inside the locked decision set in v3 §15 unless explicitly redirected.
- For non-trivial implementation work, write the regression test first and confirm it fails before writing the fix (RED → GREEN). Single-commit "test + fix" is suspicious; prefer two commits.

## How to run things

- **Local dev (no Spark):** `uv sync` then `pytest tests/`.
- **On Spark:** `source ~/.lamark/env` first. Always. The env file pins `TORCH_CUDA_ARCH_LIST=12.1`, `VLLM_USE_V1=1`, model paths.
- **Smoke test:** `python scripts/smoke_test.py --quick` for env check; without `--quick` for full decode benchmark (~15 min first run, ~5 min subsequent).
- **Format / lint:** `ruff check . && ruff format .` before commit. `mypy src/` for type check.

## Memory and persistence

- User memory lives under `~/.lamark/` on the Spark, not in the repo.
- Treat `~/.lamark/honcho.db`, `~/.lamark/lancedb/`, and `~/.lamark/sessions/` as user data — never read, copy, or transmit out of the box. Tests use isolated tmpdir fixtures.

## Reference numbers (Phase 0 envelope)

- Qwen3.6-35B-A3B FP8 single-stream decode on Spark: **~28-30 tok/s** measured (Rikkarth Apr 2026)
- Theoretical ceiling: 273 GB/s ÷ ~3 GB active = **~91 tok/s**
- Smoke-test hard fail threshold: **< 22 tok/s**
- Memory budget: ~36 GB MoE FP8 + ~14 GB dense Q4+LoRA + ~30 GB KV/buffers + ~30 GB agent runtime/headroom = fits in 119 GB usable of 128 GB unified

## When in doubt

Ask the user. Lamark is single-user, single-machine, and personal — there is no shared production environment to "not break" beyond the user's own setup. But that also means a mistake corrupts user data with no rollback besides the user's manual backups. **Measure twice, cut once.**
