# Contributing to Lamark

Lamark is in alpha. The single most valuable contribution right now is a
**real install on hardware the author doesn't own** — see below.

## The one thing that helps most: install on your hardware

Only tier S (DGX Spark) is verified. If you have a consumer GPU (4090, 5090,
3090, …) or Apple Silicon, running `install.sh` and reporting what breaks is
exactly the validation the project can't do alone. File a bug with the
template (it asks for your tier, GPU, and `lamark status`) — even "it
installed and `lamark chat` works" is a useful data point.

## Dev setup

```bash
git clone https://github.com/merocle/lamark-agent
cd lamark-agent
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 190+ tests, Spark-independent, run in <1s
```

The unit suite is deliberately Spark-independent: the model and HTTP layers
are monkeypatched, so you do **not** need a GPU, vLLM, or the heavy extras to
run or add tests.

## Ground rules

- **English-only source.** No hardcoded non-English strings in code, comments,
  prompts, or notifications. Multilingual behaviour is the model's job at
  runtime (translate via tool params / "reply in the user's language"), never
  baked into the source. This is enforced in review.
- **Vendored Hermes is upstream code.** Changes under `vendor/hermes/` must be
  a numbered `LAMARK-PATCH A.x` with inline markers and an entry in
  `vendor/hermes/MODIFICATIONS.md`. Prefer putting logic in `src/lamark/` and
  calling it from a thin guarded hook, so the upstream-merge surface stays
  small.
- **Fail-open / no-op on error** for anything wrapping a Hermes turn — a bug in
  a Lamark hook must never crash the agent.
- **Tests for behaviour changes.** New behaviour gets a test; bug fixes get a
  regression test that fails before the fix.
- **Secrets never get committed.** The redaction gate refuses to persist
  verified secrets; don't paste real keys into issues, tests, or fixtures
  (use obvious placeholders like `sk-test-...`).

## Anatomy

- `src/lamark/` — the Lamark package (CLI, training pipeline, triage,
  redaction, archive, memory). Start here.
- `scripts/` — the `lamark` dispatcher + `cmd/*.sh` subcommands + the nightly
  trainer.
- `vendor/hermes/` — vendored Hermes Agent (MIT, Nous Research). See
  `MODIFICATIONS.md`.
- `docs/` — design/readiness notes and the remediation plan.

By contributing you agree your work is released under the project's MIT license.
