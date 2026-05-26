# AGENTS.md — guidance for AI coding agents working on this repo

This file follows the [AGENTS.md convention](https://agents.md) (OpenAI /
Linux Foundation Agentic AI Foundation, 2025). It tells AI coding agents
(Claude Code, Cursor, Cline, Aider, Goose, Hermes Agent, etc.) how to
work in this repository without violating the project's load-bearing
invariants.

## Project context (1-paragraph)

Lamark is a locally-hosted personal AI agent built on a vendored copy of
**Hermes Agent** (Nous Research, MIT) at SHA `874c2b1f`. Default base
model: `Qwen/Qwen3.6-35B-A3B` (MoE, 35B-total / 3B-active) on Nvidia DGX
Spark. Architecture is layered: identity via `chat_template.jinja` (L1),
episodic memory via Hermes `memory_tool` + `USER.md`/`MEMORY.md` (L2),
style/voice via nightly LoRA fine-tuning (L4). Knowledge-edits via ROME
(L3) are planned but deferred — see `docs/v0.1-readiness-review.md`.

## Hard rules

1. **Never delete or modify the original `LICENSE`.** Nous Research's MIT
   copyright is mandatory. Add new copyright lines above, never replace.
2. **Never call the product anything other than Lamark** in user-facing
   strings. The upstream "Hermes" name appears only in `LICENSE`,
   `README.md` attribution, `vendor/hermes/`, and required MIT notices.
3. **Never bake real user data into git.** Adapters, memory files,
   training archive, and `.env` are gitignored — keep it that way.
   Verify with `git status` before commit.
4. **Never use bitsandbytes QLoRA for MoE training on DGX Spark.**
   Confirmed OOM-at-load at 4% (Kreuzhofer, NVIDIA forum). Use bf16
   LoRA only.
5. **Never enable DeepSpeed ZeRO-3 for LoRA training on Qwen3.6 MoE.**
   Breaks gradients. Use single-device or ZeRO-2.
6. **Never unfreeze the MoE router** during LoRA training. Pre-trained
   routing is load-bearing.
7. **No Russian (or any other non-English) strings in user-facing code,
   docs, or training seeds.** The repo is English-only outside
   `vendor/hermes` (third-party MIT).
8. **`vendor/hermes/` is third-party MIT code.** Only modify files
   marked `LAMARK-PATCH` and record the diff in
   `vendor/hermes/MODIFICATIONS.md`. Never rewrite upstream code in
   place without a patch marker.

## How to plan changes

- Skim `README.md` for the user-facing architecture, then
  `docs/v0.1-readiness-review.md` for what's known to be deferred or
  fragile before starting.
- For non-trivial implementation work, write the regression test first
  and confirm it fails (RED) before writing the fix (GREEN). Single
  commit "test + fix" is suspicious; prefer two commits.
- Don't add abstractions for hypothetical future needs. Three similar
  lines is better than a premature framework.

## How to run things

- **On Spark:** `~/.lamark/bin/lamark setup` for first-run; afterwards
  `lamark chat`, `lamark serve start`, `lamark status`, etc.
- **Local dev (no Spark):** `pip install -e .` in a venv. Most logic is
  pure Python; the only Spark-required parts are vLLM serving and the
  Docker-bound training dispatcher.
- **Smoke test:** `python scripts/smoke_test.py --quick` for env check;
  without `--quick` for full decode benchmark.
- **Format / lint:** `ruff check . && ruff format .` before commit.
  `mypy src/` for type check where annotations exist.

## Memory and persistence

- User memory lives under `~/.lamark/` on the host, never in the repo.
- Treat `~/.lamark/hermes-home/memories/MEMORY.md`,
  `~/.lamark/hermes-home/memories/USER.md`, `~/.lamark/archive/`, and
  `~/.lamark/adapters/` as user data — never read, copy, or transmit out
  of the box. Tests use isolated tmpdir fixtures.
- Adapters live in `~/.lamark/adapters/` (persistent). **Never write
  adapters to `/tmp/` — it gets wiped on reboot.**

## Reference numbers (Phase 1 envelope)

- Qwen3.6-35B-A3B BF16 single-stream decode on Spark: ~28-30 tok/s
- vLLM model load: ~7 minutes for 67 GB on Spark (first start) +
  ~2 minutes JIT compile + CUDA graph capture
- LoRA training (attention-only, 111 pairs × 5 epochs): ~9 minutes
- Memory budget: 67 GB BF16 base + 14 MB adapter + ~10-30 GB KV cache +
  ~10 GB agent runtime — fits in 119 GB usable of 128 GB unified.

## Layer boundaries (what is L1 vs L2 vs L3 vs L4)

- **L1 — Identity** is config (`chat_template.jinja` patched by
  `src/lamark/templates/build_template.py`). Never put identity in LoRA
  training pairs — it doesn't scale (Allen-Zhu Physics of LMs Part 3.1).
- **L2 — Episodic memory** is retrieval (`memory_tool` writes to
  `USER.md`/`MEMORY.md`, Hermes injects them into the system prompt
  each turn). Cross-session recall is the L2 contract.
- **L3 — Knowledge edits** would be ROME-style rank-1 weight surgery.
  Deferred. Don't conflate L3 with L4.
- **L4 — Style/voice LoRA** is the nightly fine-tune. Targets attention
  (q/k/v/o_proj) only on MoE bases; can target all-linear on dense bases.
  Pairs come from the archive, not hand-written seeds (after the seed
  phase ends).

## When in doubt

Ask the user. Lamark is single-user, single-machine, and personal —
there is no shared production environment to "not break" beyond the
user's own setup. A mistake corrupts user data with no rollback besides
manual backups. **Measure twice, cut once.**
