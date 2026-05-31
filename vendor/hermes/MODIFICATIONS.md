# Modifications applied on top of upstream `874c2b1f...`

This file tracks the diff stack Lamark applies to vendored Hermes. Each
entry is a logical patch (`LAMARK-PATCH A.x`), marked with a comment at
each touched line so upstream-merge conflict resolution is obvious. Git
commits in the parent repo provide the authoritative ordering.

Two kinds of change exist in the Lamark repo:
1. **In-tree patches to `vendor/hermes/`** — listed here (A.x series).
2. **A separate `src/lamark/` layer** Hermes knows nothing about — listed
   at the bottom for completeness; it is NOT a Hermes modification, so it
   never conflicts on upstream merge.

---

## Applied patches (vendor/hermes/)

### A.2 — User-facing rebrand "Hermes Agent" → "Lamark"
Files: `hermes_cli/default_soul.py`, `agent/prompt_builder.py`,
`hermes_cli/doctor.py`, `hermes_cli/skin_engine.py`, `hermes_cli/_parser.py`,
`hermes_cli/banner.py`, `hermes_cli/main.py`.
User-visible strings only (CLI prog/description, banner, default SOUL/identity,
skin branding). **Not touched**: `LICENSE` (Nous copyright verbatim),
`hermes_*` Python module names, attribution comments, `UPSTREAM.md`, the
`python -m hermes_cli` entry point.

### A.3 — Redaction gate on persisted content
Files: `tools/memory_tool.py` (`_scan_memory_content`), `tools/skill_manager_tool.py`
(`skill_manage`).
Chains `lamark.redaction.RedactionPipeline.process()` before any write to
memory/skills. Hard-blocks verified secrets (AWS/GitHub PAT/OpenAI/Anthropic
keys, JWTs, etc.) via the existing `Blocked: …` return path; PII substituted.

### A.4 — Mirror memory writes into the training archive
File: `tools/memory_tool.py`.
Every successful memory add/replace also appends a ChatML record to
`$LAMARK_HOME/archive/incoming/*.jsonl` (provenance `hermes_memory_tool:*`)
so memory updates become training signal.

### A.7 — launchd env injection (macOS gateway)
File: `hermes_cli/gateway.py`.
The launchd plist generator reads `$HERMES_HOME/env` (`export KEY=VALUE`
lines) and injects them as `EnvironmentVariables`, so secrets reach the
daemon. (Legacy/defensive after the `custom` provider migration; the
canonical path no longer needs env vars.)

### A.8 — systemd env injection (Linux gateway)
File: `hermes_cli/gateway.py` (`generate_systemd_unit`).
Adds `EnvironmentFile=-{hermes_home}/.env` and `…/env` to both user- and
system-scope units. Without this, the migrated-to-Spark gateway never saw
`TELEGRAM_BOT_TOKEN` and Telegram stayed silently offline.

### A.9 — Auto-capture training pairs from Telegram
File: `gateway/platforms/telegram.py` (`on_processing_complete`).
After each successful turn, calls `lamark.capture.pair_writer.capture_exchange`
to append the (user, assistant) pair to the archive at confidence 0.5.
Guarded import + try/except: vanilla Hermes (no `lamark` on path) and any
capture error both degrade to a no-op.

### A.10 — `ask_cloud` tool (privacy-preserving cloud escalation)
File: `tools/ask_cloud_tool.py` (new).
Lets the local model delegate a hard query to a cloud model via the user's
LiteLLM proxy. Flow: A.3 redaction (hard-fail on secrets) → Telegram approval
card (A.12) → POST to LiteLLM → audit log `$LAMARK_HOME/cloud_calls.jsonl`.
Curated model list; auto-disabled unless BOTH `LITELLM_API_KEY` and
`LITELLM_BASE_URL` are set (no proxy host is baked in — a hardcoded default
would risk routing redacted prompts to an unintended host). Redaction on the
egress path fails **closed**: any pipeline error aborts the call rather than
shipping unredacted text (only a genuine `ImportError`, i.e. running outside
the venv, passes through).
Note: does NOT send a `temperature` param (gpt-5/o-series reject non-default).

### A.11 — Register ask_cloud + train_now toolsets
File: `toolsets.py` (TOOLSETS dict).
`validate_toolset()` checks the static TOOLSETS map; tools auto-discovered by
the registry are filtered out at `get_tool_definitions()` time unless their
toolset is registered here. Adds `"ask_cloud"` (A.11) and `"train_now"` (A.14)
entries so they actually reach the agent's tools[] array.

### A.12 — Blocking gateway approval for arbitrary yes/no
File: `tools/approval.py` (`request_gateway_approval_blocking`).
Public helper reusing the dangerous-command gateway-approval machinery
(queue + threading.Event + per-session notify callback) to surface a Telegram
✅/❌ card for any tool and block until the user responds. The CLI-only
`prompt_dangerous_approval` fail-denies in a gateway worker thread (no TTY) —
this is its gateway-correct counterpart. Used by A.10 and A.14.

### A.13 — Question-intent triage hook
File: `gateway/run.py` (`_handle_message`, before the agent claims the session).
Guarded ~6-line call to `lamark.triage.apply(event)`. Classifies the message
(hybrid regex + local-LLM) and either force-grounds factual questions with a
web_search injected into `event.channel_prompt`, or injects an explicit
cloud-escalation request into `event.text` for genuinely-hard questions.
Disabled via `triage.enabled: false`; any error → no-op.

### A.14 — `train_now` tool (on-demand retrain from chat)
Files: `tools/train_now_tool.py` (new), `toolsets.py`.
The model calls it on an explicit retrain request. Shows a downtime-warning
confirmation card (A.12) — training stops vLLM (the assistant's own model)
for ~30-60 min — then launches the nightly trainer detached
(`start_new_session=True`) and returns immediately. Completion is reported by
the trainer's own curl-based Telegram notification. Card title/detail are
supplied by the model in the user's language (code stays English-only).

---

## Cross-cutting conventions

- **English-only source.** No hardcoded non-English strings anywhere in
  the patches or `src/lamark/`. User-facing text is translated at runtime by
  the model (e.g. A.14 card text via tool params; A.13 escalation directive
  tells the model to "reply in the user's language"). Input handling that
  must recognise other languages relies on the multilingual LLM (triage
  classifier, curation judge), not hardcoded patterns; the only regexes are
  English / language-neutral (cloud model names).
- **Fail-open / no-op on error.** Every patch is wrapped so a failure or a
  missing `lamark` package degrades to stock Hermes behaviour, never a crash.

---

## The `src/lamark/` layer (NOT a Hermes modification)

Standalone package layered beside Hermes; imported by the patches above but
otherwise independent (no upstream-merge surface):

- `identity.py` — single source of truth for Lamark identity (baked into the
  chat template by `templates/build_template.py`).
- `redaction/` — secrets + PII pipeline (used by A.3, A.10).
- `archive/store.py` — append-only JSONL training-pair store (A.4, A.9).
- `capture/pair_writer.py` — Telegram exchange → archive (A.9).
- `triage/{classifier,router}.py` — intent routing to web_search / ask_cloud (A.13).
- `train/{runner,dispatcher_spark,curation,eval_gate,merge_adapter}.py` —
  nightly LoRA pipeline + local-model curation (judge + synthetic decay).
- `registry.py` (+ `scripts/model-registry.yaml`) — model/tier/serving config.
- `templates/build_template.py` — inject identity into the model chat template.
- `memory/`, `inference/`, `knowledge_edit/`, `bootstrap/`, `hardware.py`,
  `cli.py` — supporting modules + the `lamark` CLI (`scripts/lamark`,
  `scripts/cmd/*`).

## Serving / infra differences (not code patches)

- FP8-quantized Qwen3.6-35B-A3B as the tier-S default (vs stock provider).
- vLLM perf flags: prefix-caching, chunked-prefill, `--reasoning-parser qwen3`,
  `--max-num-seqs 128`. DFlash speculative decoding plumbed but disabled
  (net-negative on single-user serial load).
- LoRA served via a `current` symlink under one stable `lamark` alias;
  trainer auto-cleans old adapters.
- Gateway + nightly-trainer run as systemd `--user` units on Spark (Linux),
  not launchd.
