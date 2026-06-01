# Changelog

All notable changes to Lamark are documented here. The format roughly
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/) with the
understanding that until 1.0.0 the alpha/beta tags can move.

## [Unreleased]

_Nothing yet._

## [0.1.0-alpha.1] — 2026-06-01

Second alpha — the autonomous learning loop now actually works end-to-end,
and the install path is verified on a clean machine. The authoritative
vendor-patch list is [`vendor/hermes/MODIFICATIONS.md`](vendor/hermes/MODIFICATIONS.md)
(A.2–A.4, A.7–A.14).

### Added

- **Autonomous nightly self-training loop** — auto pair-capture from Telegram
  (`LAMARK-PATCH A.9`) → local-model curation (regex pre-kill + LLM judge +
  synthetic decay) → LoRA train → eval-gate → promote/reject → Telegram
  notification. User-tunable schedule (`lamark train --schedule`).
- **Question-intent triage** (`A.13`) — factual questions get mandatory
  web-search grounding; genuinely-hard questions are escalated to the cloud.
- **`ask_cloud`** (`A.10`–`A.12`) — privacy-preserving cloud escalation via a
  user-supplied LiteLLM proxy, with a per-call Telegram approval card, PII
  redaction before egress, and an audit log.
- **`train_now`** (`A.14`) — on-demand retrain from chat with a downtime card.
- **Gateway env injection** for macOS launchd (`A.7`) and Linux systemd (`A.8`).
- **`consumed` ledger + `count_new_pairs`** — the nightly threshold counts
  genuinely-new pairs; the training set stays cumulative.

### Fixed

- **Learning loop was silently broken** — every nightly adapter after the
  first was auto-rejected because the eval-gate probed a model name vLLM
  never served (404). Reworked to promote-then-verify-then-rollback: the
  candidate is served under the `lamark` alias, gated, and rolled back via an
  EXIT trap on any failure. Verified end-to-end on Spark (good→promoted,
  failure→rolled-back). See `docs/2026-05-30-remediation-plan.md`.
- **Adapter symlinks** are now relative basenames (absolute host paths dangled
  inside the vLLM container → `LoRAAdapterNotFoundError`).
- **`ask_cloud` security** — removed a hardcoded internal proxy default
  (`LITELLM_BASE_URL` is now required); redaction fails **closed** on the
  cloud-egress path instead of passing text through on error.
- **Spark-only serving quirks** (`TORCH_CUDA_ARCH_LIST`, flash_attn purge) are
  now gated on detected hardware so non-Spark CUDA GPUs aren't broken.
- **eval-gate** — single-pass identity probe, temperature 0, and a safety
  probe that no longer false-passes a compliant "saved your secret" reply.
- **Observability** — `lamark train --status` and the Telegram cards show
  new-vs-cumulative pairs and the per-probe gate verdict.
- **Resumable model download** — `snapshot_download` is wrapped with
  retry + resume, so a network blip no longer restarts a multi-GB pull from
  zero (resolves the alpha.0 "no download resumability" limitation).
- **No more sudo cliff** — the nightly timer installs user-scope
  (`systemctl --user`) with lingering, so it works without root (resolves the
  alpha.0 "sudo dependency" limitation).
- **`lamark status`** no longer crashes under `set -u` when `$USER` is
  unbound (login-less shells / containers).

### Verified

- **Clean-room install** on a fresh `ubuntu:24.04` (no GPU): `install.sh`
  exits 0, the agent loop (`from hermes_cli.main import main`) imports with
  only the installer's dependency set, and `lamark status` exits 0. See
  `docs/smoke-tests/clean-room-install.md`.
- **Autonomous promote + rollback** on the Spark — a good adapter is promoted
  and served; a failed run rolls back to the previous adapter and stays online.

### Docs / honesty

- README throughput corrected to the measured ~48 tok/s avg (was a
  cherry-picked ~52); the factual-grounding "guarantee" reworded to
  "mechanism" (it is a prompt-level bias, not a hard constraint).
- Scrubbed internal infrastructure (LAN IP, internal proxy host, machine
  name, personal paths) from docs ahead of the public release.

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

[Unreleased]: https://github.com/merocle/lamark-agent/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/merocle/lamark-agent/compare/v0.1.0-alpha.0...v0.1.0-alpha.1
[0.1.0-alpha.0]: https://github.com/merocle/lamark-agent/releases/tag/v0.1.0-alpha.0
