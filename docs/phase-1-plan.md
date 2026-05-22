# Phase 1 implementation plan

> Locked architecture from `../../feasibility-report-v3.md` §15. This document is the TDD task ordering — what to write, in what order, with what tests proving correctness at each step.

## Entry criteria

- ✅ Phase 0 scaffolding committed (`git log` shows `872df3a Phase 0: initial scaffolding`)
- ⏳ Phase 0 smoke test PASSes on the actual Spark (`smoke_test_results/first.json` shows `severity: pass`, `median_tok_per_s ≥ 25`)
- ⏳ User has confirmed Phase 1 scope per discovery questions in v3 §10

Do not start Phase 1 implementation until both ⏳ items resolve.

## Phase 1 modules in dependency order

```
1.  CLI + config foundation     (no model dependency — pure scaffolding)
2.  Storage primitives           (LanceDB + SQLAlchemy schemas)
3.  Embeddings wrapper           (bge-m3)
4.  Memory layer (Honcho-style)  (user model + cross-session recall)
5.  Skill loop scaffolding       (FTS5, agentskills.io standard)
6.  Inference router             (vLLM client + llama.cpp client + classifier stub)
7.  Hermes Agent fork mechanics  (the actual fork + rename pass)
8.  Bootstrap wizard             (Day-0 onboarding: ChatGPT/Notes/Obsidian import)
9.  Redaction pipeline           (TruffleHog + Presidio + type-preserving substitution)
10. Eval harness                 (regression gates for future Phase 2 LoRA promotion)
11. MCP integrations             (calendar, email, filesystem, optional code/gh)
12. Channel adapters             (CLI is primary; Telegram/Signal/Web UI per user preference)
```

Modules 1-6 can be written without the Spark. Module 7 requires the upstream Hermes Agent codebase on disk (clone separately). Modules 8-12 benefit from Spark but can be partially developed locally.

## TDD recipe for each module

For every module:

1. **Write the regression test first**, exercising one specific behaviour. Test must be deterministic — no live network, no real model load, no real DGX Spark dependency unless explicitly marked `@pytest.mark.spark`.
2. **Run the test and confirm it FAILs.** If it passes before the implementation is written, the test is not a real discriminator — stop and fix the test before continuing.
3. **Implement the minimum code to pass the test.**
4. **Run the test again — confirm PASS.**
5. **Commit test (RED) and implementation (GREEN) in two separate commits.** The two-commit RED → GREEN trail in git history is evidence the discriminator was verified.

Rule of thumb on coverage:
- Pure logic (config parsing, memory schema, redaction rules): 100% line coverage expected.
- I/O layers (vLLM client, llama.cpp client, network): integration tests behind `@pytest.mark.integration` mark, run on Spark only.

## Module 1 — CLI + config foundation

**Goal:** `lamark --version` works; `lamark config show` prints resolved config from XDG paths.

**Files to create:**
- `src/lamark/__init__.py` — package metadata, `__version__`
- `src/lamark/cli.py` — typer app, `main()` entry point
- `src/lamark/config.py` — pydantic config model + loader (TOML, env vars, defaults)
- `tests/test_cli_version.py`
- `tests/test_config_loader.py`

**Tests written first (these must FAIL until implementation lands):**
- `test_lamark_version_matches_pyproject` — runs `lamark --version`, asserts it matches `pyproject.toml` version.
- `test_config_defaults_resolve_xdg_paths` — no env / no file → resolves `~/.lamark/honcho.db` etc.
- `test_config_env_overrides_defaults` — `LAMARK_HOME=/tmp/x lamark config show` reflects override.
- `test_config_file_overrides_env` — toml file > env > defaults precedence.

**Acceptance:**
- `lamark --version` prints version.
- `lamark config show` prints resolved JSON config without crashing.

## Module 2 — Storage primitives

**Goal:** LanceDB table for memory facts + SQLAlchemy schema for Honcho user model.

**Files to create:**
- `src/lamark/memory/schema.py` — SQLAlchemy models: `UserModel`, `Fact`, `Skill`, `Conversation`, `Message`
- `src/lamark/memory/store.py` — store class with CRUD methods
- `src/lamark/memory/lancedb_index.py` — vector index for cross-session search
- `tests/test_memory_schema.py`
- `tests/test_memory_store.py`
- `tests/test_lancedb_roundtrip.py`

**Tests written first:**
- `test_create_user_model_inserts_row` — given fresh DB, store creates and reads user model.
- `test_facts_have_provenance_and_timestamp` — every fact records {source, created_at, confidence}.
- `test_lancedb_search_returns_topk_by_cosine` — known embedding vectors, query returns expected top-k.
- `test_delete_user_data_cascades` — GDPR-style delete removes all related facts, conversations, embeddings.

**Acceptance:**
- Roundtrip create → read → search → delete works without Spark.

## Module 3 — Embeddings wrapper

**Goal:** `embed(text) -> np.ndarray` returns bge-m3 embeddings; CPU-only fallback works.

**Files to create:**
- `src/lamark/memory/embeddings.py`
- `tests/test_embeddings_shape.py`

**Tests written first:**
- `test_embed_returns_correct_shape` — known string, embedding is 1024-dim float32.
- `test_embed_caches_repeated_inputs` — same string twice → cached, no second model call.

**Acceptance:** bge-m3 loads via sentence-transformers; produces normalized embeddings.

## Module 4 — Memory layer (Honcho-style)

**Goal:** `memory.recall(query, k=20) -> list[Fact]` and `memory.write(fact, source)`; respects user-model invariants.

**Files to create:**
- `src/lamark/memory/recall.py`
- `src/lamark/memory/honcho.py` — user-model wrapper
- `tests/test_recall_ranks_by_relevance.py`
- `tests/test_honcho_user_model_invariants.py`

**Tests written first:**
- `test_recall_returns_at_most_k_facts` — query with many matches, capped at k.
- `test_recall_uses_combined_vector_plus_recency` — older fact with high similarity loses to newer fact with similar similarity (configurable recency-weighting coefficient).
- `test_user_model_persona_locked_until_explicit_overwrite` — agent self-edits cannot silently replace user-confirmed persona facts.

**Acceptance:** `recall()` returns relevant Facts deterministically given fixture data.

## Module 5 — Skill loop scaffolding

Stub for now; skill execution is Phase 1c. Just the schema + FTS5 index + `skill_search(query)` returning matching skill names.

## Module 6 — Inference router

**Goal:** Given user prompt + assembled system context, route to one of two backends and return generated text.

**Files to create:**
- `src/lamark/inference/router.py` — routing logic
- `src/lamark/inference/vllm_client.py` — OpenAI-compatible client (vLLM serves OpenAI API)
- `src/lamark/inference/llamacpp_client.py` — llama.cpp server client (also OpenAI-compatible)
- `src/lamark/inference/classifier.py` — small-model heuristic stub (Phase 1: regex/length rules; Phase 2: Qwen3 0.6B classifier)
- `tests/test_router_dispatches_correctly.py`

**Tests written first:**
- `test_short_chat_routes_to_moe` — prompt < 200 tokens → MoE backend.
- `test_writing_keyword_routes_to_dense` — prompt mentions "write me", "compose", "draft" → dense backend (when Phase 2 LoRA exists).
- `test_router_falls_back_on_backend_unavailable` — if dense not ready, falls back to MoE with warning.

**Acceptance:** router decides based on prompt features; clients return generated text given a running backend (mocked in unit tests; live in integration tests on Spark).

## Module 7 — Hermes Agent fork

**Goal:** Clone Hermes Agent code into `src/lamark/agent/`, rename user-facing strings, integrate with our memory+inference modules.

**Procedure (one-shot, ~5-7 days work):**
1. `git clone https://github.com/NousResearch/hermes-agent /tmp/hermes-upstream`
2. Copy core agent loop, skill executor, channel adapter framework into `src/lamark/agent/`
3. Find/replace `hermes` → `lamark` in user-facing strings (not LICENSE, not attribution).
4. Replace Hermes's memory implementation with our `src/lamark/memory/` (Modules 2-4).
5. Replace Hermes's model provider integration with our `src/lamark/inference/router.py`.
6. Smoke-test: `lamark chat "hello"` → agent loads, retrieves memory, calls router, returns reply.

**Critical not-to-do:**
- Do not modify LICENSE.
- Do not preserve Hermes-specific telemetry, opt-in/out, or analytics.
- Do not pull Hermes's Honcho fork directly — we wrote our own per Module 4 to fit DGX Spark constraints.

## Module 8 — Bootstrap wizard

Day-0 onboarding. See v3 §8.

**Files to create:**
- `src/lamark/bootstrap/wizard.py` — interactive flow
- `src/lamark/bootstrap/importers/chatgpt.py` — ChatGPT export `.json` parser
- `src/lamark/bootstrap/importers/notes_apple.py` — Apple Notes export parser
- `src/lamark/bootstrap/importers/obsidian.py` — Obsidian vault walker
- `tests/bootstrap/test_chatgpt_import_extracts_facts.py`
- `tests/bootstrap/test_obsidian_walker_respects_gitignore.py`

**Tests written first:**
- `test_chatgpt_import_creates_facts_from_known_export` — given fixture export, count of extracted facts matches expected.
- `test_apple_notes_import_redacts_pii` — known PII in fixture is redacted before storage.
- `test_obsidian_walker_skips_attachments_folder` — large binaries excluded.

**Acceptance:** end-to-end run: `lamark bootstrap --chatgpt export.json --obsidian ~/vault` → memory has 50-200 facts, redaction log audited.

## Module 9 — Redaction pipeline

Per v3 §7 (Phase 2 nightly job) — but the same pipeline is used at bootstrap. Implement once, use twice.

**Files to create:**
- `src/lamark/redaction/pipeline.py` — orchestrator
- `src/lamark/redaction/secrets.py` — TruffleHog wrapper + literal denylist
- `src/lamark/redaction/pii.py` — Presidio wrapper
- `src/lamark/redaction/substitute.py` — type-preserving substitution via local Qwen
- `tests/redaction/test_blocks_known_secrets.py`
- `tests/redaction/test_pii_substitution_preserves_utility.py`

**Critical test:** `test_aws_key_in_input_blocks_pipeline` — pipeline halts and audit-logs if TruffleHog finds verified secret. This is a non-negotiable safety gate.

## Module 10 — Eval harness

Stubbed for Phase 1; full implementation in Phase 2 when LoRA promotions need gating.

**Phase 1 deliverable:** eval CLI that runs MMLU/GSM8K/IFEval subsets against a backend and emits pass/fail + per-task scores. No "promote" action yet.

## Module 11 — MCP integrations

Lift from Hermes Agent (inherits MCP runtime). Wire Lamark-specific MCP servers:
- Calendar (Google Calendar / iCloud — choose at user preference)
- Email (IMAP)
- Filesystem (limited to user-allowed roots)
- Optional: `gh` CLI for GitHub workflows

## Module 12 — Channel adapters

CLI is the default (already in Module 1). Add channels in order of user demand:
1. **Web UI** — local web (single-user, no auth needed for localhost-only).
2. **Telegram / Signal** — only if user explicitly wants out-of-home access (requires reverse-tunnel — separate work).
3. Out of scope for Phase 1.

## Exit criteria for Phase 1 (= entry criteria for live use)

- `lamark bootstrap` runs end-to-end with at least one import path.
- `lamark chat "<query>"` round-trips: memory retrieval → router → backend → response with retrieved facts present in system prompt.
- `lamark memory show` lists current user-model facts.
- Smoke test still PASSes on Spark.
- All Module 1-9 tests green; Module 10-12 tests at least exist (may skip).
- Phase 2 trigger documented in `docs/phase-2-trigger.md`: when to start collecting style training pairs, what UX signal to watch for.

## Phase 1 budget

- Modules 1-4 (local, no Spark): **1-2 weeks**
- Modules 5-6 (local + Spark for integration): **1-2 weeks**
- Module 7 (Hermes fork integration): **1-2 weeks** — biggest risk module
- Modules 8-9 (bootstrap + redaction): **1-2 weeks**
- Modules 10-12 (eval + MCP + channels): **1 week**
- Buffer: **1 week** for Spark-specific debugging (eager-loader, FP8 quantization, vLLM corner cases)
- **Total: 6-10 weeks** for Phase 1 alone.
