# Changelog

All notable changes to Lamark are documented here. The format roughly
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/) with the
understanding that until 1.0.0 the alpha/beta tags can move.

## [Unreleased]

Nothing committed since v0.1.0-alpha.0.

## [0.1.0-alpha.0] — 2026-05-25

First public alpha. Verified end-to-end on two independent DGX Spark hosts
(NVIDIA GB10, 128 GB unified memory, sm_121). Consumer GPU support is
wired but unverified — see `docs/v0.1-readiness-review.md` for the honest
assessment.

### Added

- **`install.sh`** — self-contained one-command installer (`curl | bash`).
  Preflight checks OS/arch/GPU/docker/python3/git, idempotent clone +
  venv + Python deps + dispatcher symlink + PATH inject.

- **Unified `lamark` CLI dispatcher** — single entry point routing to
  subcommand scripts under `scripts/cmd/`:
  - `lamark setup` — three-branch wizard (Local / Existing endpoint / Cloud-first)
  - `lamark chat` — interactive REPL via Hermes Agent
  - `lamark serve {start|stop|status|restart}` — local vLLM lifecycle
  - `lamark status` — one-screen health overview
  - `lamark switch-base <model>` — change default base from the registry
  - `lamark train {--now|--status|--config}` — manual retrain control
  - `lamark config {get|set|show}` — edit `$HERMES_HOME/config.yaml`
  - `lamark logs vllm|nightly|download|chat` — tail recent logs

- **Layered memory architecture** documented and partially implemented:
  - **L1 identity via `chat_template.jinja`** — `IDENTITY_PROMPT` injected
    at the system-message slot; works on any base model, no LoRA required
  - **L2 episodic memory** — Hermes `memory_tool` + `USER.md`/`MEMORY.md`,
    cross-session recall verified
  - **L3 knowledge edits** — deferred to Phase 2 (~50 LOC patch to
    EasyEdit for transformers 5.x compat)
  - **L4 style/voice LoRA** — pipeline works, scale validation deferred
    to when users actually accumulate 1000+ turns

- **Model registry + hardware tier dispatch**
  (`scripts/model-registry.yaml` + `src/lamark/hardware.py` +
  `src/lamark/registry.py`) — auto-detect GB10/sm_121/RAM → tier S/M/L/XS,
  per-tier model recommendations, `tested: true|false` flag per entry.

- **Hybrid retrain trigger** — `lamark setup` asks frequency
  (daily/weekly/manual) and `min_pairs` threshold (default 50). Systemd
  timer fires per cadence but the actual training skip-checks against
  threshold; history persisted to `~/.lamark/train-history.jsonl`.

- **Eval gate** for newly-trained adapters — three smoke probes
  (identity / safety / coherence) gating adapter promotion.

- **`build_template.py`** — patches the model's `chat_template.jinja` to
  inject `IDENTITY_PROMPT` AND force `enable_thinking=false` (suppresses
  Qwen3 reasoning leakage in chat output).

- **`LAMARK-PATCH A.4`** — Hermes `memory_tool` writes are mirrored into
  `~/.lamark/archive/` for nightly training input.

- **Vendored Hermes Agent** at SHA `874c2b1f` (MIT, Nous Research) with
  `LAMARK-PATCH A.2` (rebrand) + `A.3` (redaction) + `A.4` (archive
  mirror) + `A.6` (license dual-attribution) applied.

### Verified

- L1 identity manifestation **without any client-supplied system prompt**,
  across two independent Sparks running different vLLM images
  (custom-built `lamark/vllm:25.10` and upstream `vllm/vllm-openai:v0.21.0`).

- L2 cross-session memory recall (session 1 states a fact → session 2
  fresh AIAgent answers correctly from `USER.md`).

- MoE + LoRA + tool-call combo on `vllm/vllm-openai:v0.21.0` (the
  `FusedMoE3DWithLoRA` bug from earlier nightlies is fixed in upstream
  stable).

- Cycle 4 training (111 pairs × 5 epochs, loss 1.57, 9 minutes on Spark)
  — saves persistent adapter to `~/.lamark/adapters/identity-cycle4/`.

### Known limitations (intentionally deferred)

- **Only tier S verified.** Tiers M/L/XS (4090/5090/3090/Mac) are wired
  but not run on actual non-Spark hardware. `lamark setup` warns and
  asks for confirmation when picking an experimental tier.

- **L3 ROME knowledge editing not built.** Research-confirmed feasible
  via ~50 LOC patch to EasyEdit's `nethook.py`/`compute_v.py`/`repr_tools.py`
  for transformers 5.x dict-output compat, but out of scope for v0.1.

- **L4 LoRA at scale unvalidated.** Pipeline works for ~100 pairs; Allen-Zhu's
  extractability threshold needs ~1000 paraphrases per fact for unconditional
  recall. We're 10× below that — Phase 2 will scale.

- **Plaintext archive.** `~/.lamark/archive/` stores conversations as plain
  JSONL. Phase 2 adds opt-in age-encryption with a passphrase.

- **No HF download resumability.** A flaky connection mid-download forces
  restart from zero. Phase 2 adds resumable + checksum-verified pulls.

- **Cycle 1-3 adapters lost** on Spark-01 reboot because they lived in
  `/tmp/`. Fixed architecturally (adapters now in `~/.lamark/adapters/`),
  but a reminder that persistence wasn't audited end-to-end.

- **Sudo dependency.** Systemd timer installation needs sudo. Without
  it, users can copy unit files manually or use `lamark train --now` on
  demand.

### Removed

- All Russian-language Q&A pairs from `seed_identity*.py` and
  `seed_session.py`. Repository is now English-only outside `vendor/hermes`
  (third-party MIT code, untouched).

- `docs/hermes-vs-lamark-analysis.md` — early-session architecture analysis
  with bilingual quotes; archival, not load-bearing for v0.1.

[Unreleased]: https://github.com/Merocle/lamark-agent/compare/v0.1.0-alpha.0...HEAD
[0.1.0-alpha.0]: https://github.com/Merocle/lamark-agent/releases/tag/v0.1.0-alpha.0
