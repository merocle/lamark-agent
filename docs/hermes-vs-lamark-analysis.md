# Hermes Agent vs Lamark — Integration Decision

**Date:** 2026-05-23
**Status:** Phase B-write round 1 (pre-Module-7-integration)
**Driver:** User question — "зачем нам SQL база? вроде Hermes умеет набирать инфомрацию о пользователе и сохранять её"

## Executive summary

**The user's question is well-founded but the answer is "no, Hermes does not enforce the invariants we care about by default."**

Investigation conducted via 4 parallel research agents over the cloned Hermes upstream at `/tmp/hermes-upstream/` (NousResearch/hermes-agent @ MIT, ~162k stars). Each agent verified claims against actual source code with file:line citations.

**Headline findings:**

1. **Hermes's README oversells "Honcho dialectic user modeling."** Honcho is an **optional plugin** requiring a SaaS API key (`HONCHO_API_KEY`) and `honcho-ai==2.0.1` from the `[honcho]` pyproject extra. By default Hermes ships with two plain markdown files (`MEMORY.md`, `USER.md`) under `~/.hermes/memories/`, manipulated via a `memory` tool with `add`/`replace`/`remove`/`read` actions. No schema, no provenance, no persona-lock, no GDPR cascade. (`tools/memory_tool.py:138-217`)

2. **An optional `holographic` plugin exists** at `plugins/memory/holographic/` with a richer SQLite schema (`facts(content UNIQUE, category, tags, trust_score, hrr_vector BLOB)` + entities + FTS5), but: (a) it has **no UserModel table**, (b) `trust_score` is a free-running float with no provenance enum, (c) no persona-lock against agent self-edits, (d) no atomic `delete_user_data(confirm=True)` API. Stronger than the markdown default but still doesn't enforce our invariants. (`plugins/memory/holographic/store.py:16-76`)

3. **Of our 7 Lamark modules, zero map cleanly to "Hermes already does this — drop ours":**
   - 5 modules are **new capabilities** (skill FTS5, prompt-content router, Day-0 wizard, ChatGPT importer, Obsidian importer)
   - 2 modules are **override-only** (memory schema, redaction pipeline) — Hermes has partial equivalents but lacking our load-bearing invariants

4. **What Hermes uniquely brings (genuinely valuable, would take months to rebuild):**
   - **26 working platform adapters** in `gateway/platforms/` (Telegram, Slack, Matrix, WhatsApp, Signal, Email, SMS, DingTalk, Feishu, WeCom, Mattermost, BlueBubbles, etc.) + 6 plugin platforms (Discord, Teams, Line, IRC, SimpleX, Google Chat)
   - **8 terminal/sandbox backends** in `tools/environments/` (Local, Docker, SSH, Singularity, Modal, Managed-Modal, Daytona, Vercel Sandbox)
   - **Conversation lifecycle** — `agent/conversation_loop.py:232 run_conversation()`, 4191 lines, with proper persistence checkpoints, memory prefetch hooks, tool call dispatch, background skill review
   - **Cron scheduler** for unattended jobs (`cron/scheduler.py`, 1972 lines)
   - **Subagent delegation** (`tools/delegate_tool.py`, 2801 lines, parallel children via ThreadPoolExecutor)
   - **Autonomous skill creation** — real, via background-review fork after every N iterations (`agent/background_review.py`, `agent/curator.py`)
   - **Skill ecosystem** — 89 SKILL.md bundles, agentskills.io frontmatter convention, plugin-bundled skills
   - **MemoryProvider ABC** (`agent/memory_provider.py:42`) — the integration seam we can plug Lamark's MemoryStore into without forking Hermes's internals

## Integration plan for Module 7

**Strategy: Fork Hermes; register Lamark MemoryStore as a custom `MemoryProvider`; disable Hermes's built-in MEMORY.md store via config; keep our CLI/wizard/importers/redaction as Lamark-side entry points.**

### What we override (replace Hermes's default)

| Hermes default | Replace with | Reason |
|---|---|---|
| `tools/memory_tool.py` MEMORY.md/USER.md flat-file store | Lamark MemoryStore via MemoryProvider | Our invariants: UserModel singleton, persona-lock, Fact provenance enum, confidence ∈ [0,1], GDPR cascade. None of these exist in `memory_tool.py`. |
| `agent/redact.py` log-masking (no HALT) | Lamark RedactionPipeline at persistence boundaries | Hermes redacts for logs (mask-and-continue). Our pipeline raises `SecretFound` to abort imports before any DB write. Different layer, different purpose. Keep Hermes's log filter in place for log output. |

**Mechanism:** Hermes already has the `MemoryProvider` ABC (`agent/memory_provider.py:42`) with hooks `initialize`, `prefetch`, `sync_turn`, `on_pre_compress`, `on_session_end`. Registered providers are loaded via `memory.provider` config in `~/.hermes/config.yaml` (`plugins/memory/__init__.py:308-320`). We register `lamark` as a provider; Hermes's MemoryManager (`agent/memory_manager.py:244`) will call our methods. **We do not need to modify Hermes core code** — we just add `plugins/memory/lamark/` directory in the fork.

### What we keep from Hermes as-is

- Gateway + 26 channel adapters (`gateway/platforms/*`)
- 8 terminal backends (`tools/environments/*`)
- Conversation lifecycle (`agent/conversation_loop.py`, `run_agent.py`)
- Skill ecosystem (`agent/skill_utils.py`, `tools/skills_tool.py`, `agent/curator.py`, `agent/background_review.py`) — but **we add** our FTS5 `skill_search` as a NEW tool the agent can call when its in-prompt scan misses
- Cron scheduler (`cron/*`)
- Subagent delegation (`tools/delegate_tool.py`)
- `auxiliary_client.py` provider-fallback chain — keep for credit-exhaustion handling; **bypass for our local vLLM path** by setting the local endpoint as the primary provider
- Bootstrap wizard `hermes_cli/setup.py` for config (model/API-key/sandbox) — but **add our Day-0 wizard** as a separate `lamark bootstrap` command for memory-side onboarding (different concern)

### What we keep from Lamark as-is

- `src/lamark/memory/` — wired through MemoryProvider seam
- `src/lamark/inference/router.py` — prompt-content two-backend router (Hermes's `auxiliary_client` is provider-fallback, not prompt-routing). The router calls into Hermes's transport layer internally.
- `src/lamark/inference/client.py` — **review at integration time**, may be redundant with Hermes's OpenAI SDK calls. Decision deferred to actual integration.
- `src/lamark/bootstrap/` — wizard + ChatGPT + Obsidian importers, run as Lamark commands before Hermes session starts
- `src/lamark/redaction/` — used by importers + a wrapper around persistence writes

### What we drop or merge from Lamark

- **None outright dropped.**
- Phase 2 candidate: port Hermes's holographic retrieval scoring (FTS5 + Jaccard + HRR vector + trust + temporal decay; `plugins/memory/holographic/retrieval.py:48-112`) into our `recall.py`. Our public contract (`list[Fact]` ranked best-first) is already designed for swap.
- **Merge candidate:** Hermes's secret regex list at `agent/redact.py` covers more vendor prefixes (xox*, AIza, pplx-, etc.) than our `secrets.py`. At integration, merge those into `lamark/redaction/secrets.py`.

## Module-by-module verdict table

| Module | Verdict | Justification (one line) |
|---|---|---|
| 2 — Memory schema + Store | **KEEP_OURS_AS_OVERRIDE** | Neither Hermes path enforces UserModel singleton, provenance enum, persona-lock, or GDPR cascade. |
| 4 — recall() | **KEEP_OURS_AS_OVERRIDE** | Hermes default has no recall (static markdown dump). Holographic recall is stronger but bound to its own schema; port later. |
| 5 — Skill FTS5 search | **KEEP_OURS_NEW_CAPABILITY** | Hermes skills are markdown files discovered by FS walk + in-prompt index + LLM choosing. No SQLite/FTS5/bm25. |
| 6 — InferenceRouter | **KEEP_OURS_NEW_CAPABILITY** | Hermes's router is provider-fallback (credits/availability), not prompt-content two-backend routing. |
| 6b — OpenAIChatClient | **REVIEW_AT_INTEGRATION** | May be redundant with Hermes's OpenAI SDK calls. Keep until proven dead. |
| 8 — Bootstrap wizard | **KEEP_OURS_NEW_CAPABILITY** | `hermes_bootstrap.py` is a Windows UTF-8 fix. `agent/onboarding.py` is contextual hints, not Day-0 profile collection. |
| 8b/8c — ChatGPT/Obsidian importers | **KEEP_OURS_NEW_CAPABILITY** | Hermes migration is OpenClaw→Hermes only. No third-party history import. |
| 9 — Redaction pipeline | **KEEP_OURS_AS_OVERRIDE** | Hermes redacts for logs (mask, no HALT, no email/CC PII, no audit sink, no deny_phrases). |

## Risks and open questions

### R1. MemoryProvider plugin contract may have constraints we haven't seen.
The `MemoryProvider` ABC has hooks `initialize`, `prefetch(query)`, `sync_turn`, `on_pre_compress`, `on_session_end`. If `prefetch(query)` is expected to return a specific shape (e.g. text-block string vs structured list[Fact]), our recall() may need a thin adapter. **Action:** read `agent/memory_provider.py:42` end-to-end and the existing plugins (`plugins/memory/holographic/__init__.py`) to confirm the contract before Module 7 starts.

### R2. Conversation persistence boundary.
Hermes's `_persist_session` flushes messages to its own SQLite (`~/.hermes/state.db` via `hermes_state.py:186-308`) at multiple checkpoints. **Our user data is in `~/.lamark/honcho.db` via our MemoryStore.** Two databases on disk side-by-side is acceptable (different concerns: session message history vs personal memory facts), but we must ensure they don't drift in ownership semantics. **Action:** decide explicitly whether Lamark MemoryStore also gets a "this Fact came from session N message M" backlink to Hermes's state.db, or whether they're fully independent.

### R3. Persona-lock + agent skill curation may conflict.
Hermes's background-review fork creates new SKILL.md files autonomously after every N iterations. If a skill writes back to memory (e.g. "remembered that user prefers X"), it bypasses our PROVENANCE_USER_EXPLICIT check on persona fields. **Action:** wire the MemoryProvider's `sync_turn` so any write originating in a background-review fork is forced to `PROVENANCE_AGENT_SELF_EDIT`. Our persona-lock then refuses the write automatically; the agent will see the rejection and adjust.

### R4. License compatibility check before merging Hermes prefix list into our secrets.py.
Hermes is MIT, ours is MIT — OK, but the regex list itself was likely derived from various OSS projects (TruffleHog, etc.) some of which may be Apache-2 or BSD. **Action:** when merging, preserve any per-pattern attribution comments from Hermes if present, and double-check `agent/redact.py` git blame for original sources.

### R5. The `auxiliary_client.py` provider chain assumes credit-paid endpoints.
For us, the "provider" is local vLLM at `127.0.0.1:8000`. Hermes's auxiliary_client expects HTTP 402 / credit errors as a fallback trigger — these won't fire on local. **Action:** verify that pointing Hermes at a local OpenAI-compatible endpoint works without the fallback machinery getting in the way. If problematic, register a `LocalOnlyTransport` that short-circuits the chain.

### R6. Hermes assumes `~/.hermes/` not `~/.lamark/`.
Conflict on disk layout. Two options: (a) fork sets `HERMES_HOME=$LAMARK_HOME/hermes` so everything goes under our home, (b) fork renames internal constants. Option (a) is one-line, option (b) is a full find-replace pass. **Recommend (a)** for the first cut.

### R7. Background-review fork may bypass our redaction pipeline.
Hermes's background_review creates new SKILL.md files via `skill_manage(action=create)`. If those files end up containing user PII (e.g. a skill named "weekly_summary_for_anna@example.com"), they're not redacted. **Action:** wrap `skill_manage` write paths with our redaction pipeline.

## Implementation order for Module 7

1. **Fork.** `git clone https://github.com/NousResearch/hermes-agent vendor/hermes` (committed as a vendor copy with explicit upstream SHA pin)
2. **Set HERMES_HOME → $LAMARK_HOME/hermes** in our env file
3. **Add `vendor/hermes/plugins/memory/lamark/__init__.py`** — a new `MemoryProvider` subclass delegating to our existing `MemoryStore` + `recall()`
4. **Set `memory.provider: lamark`** in Hermes config; disable built-in MEMORY.md store
5. **Wire `auxiliary_client` → local vLLM endpoint** for the primary provider chain
6. **Wrap `skill_manage` write paths** with redaction (or set a hook)
7. **Add `lamark bootstrap` and `lamark chat` CLI commands** that compose Lamark + Hermes behaviour (delegate to Hermes's `run_conversation` after our memory injection)
8. **End-to-end smoke test:** `lamark chat "что ты обо мне знаешь?"` → Hermes's conversation_loop invokes our MemoryProvider.prefetch() → our recall() returns top-K Facts → Hermes injects them into the system prompt → vLLM responds with awareness of seed facts

Expected effort: **3-5 days** for steps 1-6 (mostly reading Hermes code + writing a single MemoryProvider adapter), then **2-3 days** for steps 7-8.

## Round-1 rebuttal (post Phase B-review)

Three parallel critics reviewed v1 of this document. **Their findings significantly revise the verdict matrix.** Headline: many of our v1-claimed invariants are decorative; Hermes's default has more production guardrails than we acknowledged; the timeline was 2-3× optimistic.

### Critic 1 — factual accuracy (verifying every file:line)

Convergent confirmations:
- ✓ MemoryProvider ABC exists at `agent/memory_provider.py:42`
- ✓ Built-in MEMORY.md store at `tools/memory_tool.py:138` (690-line file)
- ✓ Holographic schema verified verbatim
- ✓ Honcho is optional plugin with SaaS API key
- ✓ `conversation_loop.py` 4191 lines, `cron/scheduler.py` 1972, `tools/delegate_tool.py` 2801
- ✓ Lamark persona-lock + Fact CheckConstraint + deny_phrases at cited locations

Corrections to v1 claims:
- **Critical:** prefetch returns `str` (formatted text block), NOT `list[Fact]`. Lamark recall returns `list[Fact]`. **Adapter needed** to format Facts into text. (v1 R1 had no resolution.)
- **Critical:** `~/.lamark/honcho.db` was a stale artifact from when Honcho was a design influence. Lamark MemoryStore is path-parameterized — no hardcoded filename.
- **Critical:** `tools/lazy_deps.py` (613 lines) — Hermes's runtime auto-install system — was completely omitted from v1. **It conflicts with Lamark's eager-loader-patch invariant.** Must address.
- **Important:** `_persist_session` lives in `run_agent.py:1171`, not `hermes_state.py` (v1 R2 mis-cited).
- **Important:** `auxiliary_client.py` is **5289 lines**, not a thin shim. Plan step 5 ("Wire auxiliary_client → local vLLM") is non-trivial.
- **Important:** MemoryManager enforces **one external provider at a time**. Adding Lamark displaces Honcho/Mem0/holographic.
- **Important:** Tests directory is 30+ subdirs with hundreds of files. CI inheritance is real work.
- **Suggestion:** 20+6=26 adapters, not 26+6 (v1 inflated by 6). Skill count is 171, not 89 (v1 underreported).

### Critic 2 — steelman DROP_OURS (the most consequential critic)

**Critic 2 made the strongest argument: our v1 KEEP_OURS_AS_OVERRIDE is over-broad.**

Hermes's `tools/memory_tool.py` (690 lines) has guardrails we don't:
- Atomic writes with `os.replace` + per-target `flock` (`memory_tool.py:175-210`)
- External-drift detection with `.bak.<ts>` snapshots refusing to clobber out-of-band edits (`memory_tool.py:482-519`)
- Prompt-injection scanner refusing entries matching `_MEMORY_THREAT_PATTERNS` (`memory_tool.py:100-105`)
- Frozen system-prompt snapshot preserving prefix cache (`memory_tool.py:155, 410-421`)
- Char budget enforcement (`memory_char_limit=2200`)
- Dedup-on-load (`memory_tool.py:166-167`)

Lamark's `MemoryStore.add_fact()` (`src/lamark/memory/store.py:140-162`) has **none** of these.

**Our claimed invariants are mostly write-only / decorative under current code:**

| Invariant | Status |
|---|---|
| Single-row UserModel | Defended by UNIQUE constraint, but **no realistic threat exists** in single-user mode |
| Persona-lock against agent_self_edit | Defended by `PermissionError`, but **zero code outside the wizard ever calls `update_user_model_field`**. Guards an attack surface that doesn't exist yet |
| Provenance enum (4 values) | **No downstream code branches on `Fact.source`**. recall.py doesn't filter; CLI doesn't expose `--source=...`. **Write-only — audit trail with no auditor** |
| Confidence ∈ [0,1] | **Not in `recall()` formula** (`src/lamark/memory/recall.py:48-93` weights only relevance + recency). CHECK constraint prevents impossible values, but no downstream code reads it |
| GDPR cascade `delete_user_data(confirm=True)` | **Strictly worse** than Hermes's `memory(action=remove, content="X")` for the realistic "forget Berlin" use case — we don't have per-fact delete |

**The one genuinely safety-relevant invariant lives in redaction/, not memory/:** our `RedactionPipeline.process()` raises `SecretFound` to abort imports before any DB write. Hermes's `memory_tool.py` threat scanner only refuses individual entries — doesn't HALT a multi-file import.

### Critic 3 — implementation complexity

Convergent with Critic 1, but more concrete:

- **MemoryProvider ABC has 17 methods**, not 5. Three abstract (`name`, `is_available`, `initialize`, `get_tool_schemas`), 13 optional but load-bearing (`prefetch`, `sync_turn`, `on_pre_compress`, `on_session_end`, `on_session_switch`, `on_memory_write`, `on_delegation`, `on_turn_start`, `queue_prefetch`, `handle_tool_call`, `shutdown`, `system_prompt_block`, `get_config_schema`, `save_config`).
- **"Disable Hermes built-in MEMORY.md store"** has no config flag. Requires tool-list filter or monkey-patch on `tools/registry.py`.
- **Holographic plugin template is not 408 lines**; it's `__init__.py` (408) + `store.py` (578) + `retrieval.py` (593) + `holographic.py` (203) ≈ 1780 lines.
- **Honcho plugin template is 1328 lines** — closer to what feature-complete looks like.
- `on_session_switch()` fires on `/resume`, `/branch`, `/reset`, `/new`, AND **context compression** — 5 entry points reassigning `agent.session_id` without tearing down the provider. **If we key facts by session_id, facts leak between sessions** unless we handle this.
- HERMES_HOME has **1931 grep hits**; subprocess spawners (gateway, cron, delegate) need explicit propagation. Not "one config line."
- Hermes's tools have a `set_secret_capture_callback` for input secrets, but **no equivalent for skill content written to disk**. R7 (wrapping `skill_manage`) requires subclass + monkey-patch, not a hook.

**Honest time re-estimate: 11-18 working days for a single engineer, median 14 days.**

| Step | v1 estimate | Critic-3 estimate | Why |
|---|---|---|---|
| 1. Fork + vendor copy | <1d | 0.5d | OK |
| 2. HERMES_HOME plumbing | <1d | 1d | Subprocess propagation |
| 3. MemoryProvider adapter | 1-2d | **3-4d** | 17 methods, not 5; recall→text adapter |
| 4. Disable built-in MEMORY.md | <1d | 1-2d | Undocumented; tool-list filter |
| 5. Wire vLLM into auxiliary_client | <1d | **1-2d** | 5289-line beast |
| 6. Wrap skill_manage redaction | <1d | **1-2d** | No hook; subclass + monkey-patch |
| 7. `lamark bootstrap` + `lamark chat` CLI | 1-2d | 2-3d | Lifecycle ordering tests |
| 8. End-to-end smoke + bugfix | 1d | **2-3d** | 5 lifecycle entry points × 2 DBs |
| **Total** | **5-8d** | **11-18d** | |

## Revised verdict matrix (v2)

Critic 2's HYBRID-C narrowing is **accepted**. New verdict:

| Module | v1 verdict | v2 verdict | Rationale |
|---|---|---|---|
| 2 — UserModel table | KEEP_OURS_AS_OVERRIDE | **DROP — fold into Hermes USER.md** | Singleton invariant is decorative in single-user mode; Hermes already frozen-snapshots USER.md. Persona-lock had no real-world callers. |
| 2 — Fact table | KEEP_OURS_AS_OVERRIDE | **KEEP — narrower scope** | Provenance + confidence kept FOR FUTURE recall@k upgrade (Phase 2). Drop persona-lock branch from source enum. Add `delete_fact_where(query)` to match Hermes's per-entry remove ergonomics. |
| 4 — recall() | KEEP_OURS_AS_OVERRIDE | **KEEP — but defer activation** | Phase 1: let Hermes's natural memory-tool retrieval handle the agent loop. Phase 2: activate our recall() once embeddings (bge-m3) land. |
| 5 — Skill FTS5 search | KEEP_OURS_NEW_CAPABILITY | **KEEP — same verdict** | Hermes has no equivalent. |
| 6 — InferenceRouter | KEEP_OURS_NEW_CAPABILITY | **KEEP — same verdict** | Prompt-content two-backend routing; Hermes only has provider-fallback. |
| 6b — OpenAIChatClient | REVIEW_AT_INTEGRATION | **DROP — Hermes already has OpenAI SDK** | Confirmed redundant once we point at Hermes's transports. |
| 8 — Bootstrap wizard | KEEP_OURS_NEW_CAPABILITY | **KEEP — writes USER.md + Facts** | Hermes setup wizard covers config, not memory seeding. Our wizard now writes to USER.md AND our Fact table (the parts that aren't decorative). |
| 8b/8c — Importers | KEEP_OURS_NEW_CAPABILITY | **KEEP — same verdict** | Hermes has no third-party history import. |
| 9 — Redaction pipeline | KEEP_OURS_AS_OVERRIDE | **KEEP_AS_HOOK — the one genuinely load-bearing invariant** | HALT-on-secret is the only Lamark guarantee that's both load-bearing and absent from Hermes. Wire as `on_memory_write` hook on the provider AND a pre-write filter on `skill_manage`. Merge Hermes's broader secret regex prefixes into ours. |

**Net code change for Lamark:** ~40% reduction in `src/lamark/memory/`. Drop `UserModel` table + persona-lock enforcement + `update_user_model_field`. Add `delete_fact_where()`. Keep `Fact`, recall(), Skill FTS5 as-is.

## New risks surfaced by critics

- **R8 (Critical, Critic 1):** `tools/lazy_deps.py` runtime-install conflicts with our eager-loader-patch. Action: either disable Hermes's lazy_deps in our fork, or wrap with our patch.
- **R9 (Critical, Critic 1):** prefetch returns `str`, not `list[Fact]`. Build a formatter that takes our recall() output and emits a `<lamark_memory>...</lamark_memory>` text block matching Hermes's convention.
- **R10 (Important, Critic 3):** `on_session_switch()` fires on 5 distinct events. Define provider behaviour for each — session_id rotation, fact scoping, etc.
- **R11 (Important, Critic 3):** Hermes tests assume `~/.hermes/` paths and may break under our `HERMES_HOME=$LAMARK_HOME/hermes` redirect. Decide before forking: run subset of `tests/plugins/memory/` only.
- **R12 (Important, Critic 2):** Char budget. Hermes's prefetch output gets truncated at `memory.memory_char_limit=2200` chars. If our recall returns more, it's silently chopped. Either honour the budget or document the cap.
- **R13 (Important, Critic 3):** Tool-name collision. Hermes already owns `memory`, `fact_store`, `honcho_profile`, etc. **Namespace our tools as `lamark_recall`, `lamark_remember`, `lamark_forget`.**

## Recommended de-risk spikes (before Module 7 starts)

1. **MemoryProvider adapter spike** — fork Hermes, write a 50-line `LamarkMemoryProvider` returning hardcoded text from `prefetch()`. Confirm `lamark_recall` tool surfaces in agent loop. **0.5 day.**
2. **Built-in `memory_tool.py` suppression spike** — confirm we can filter Hermes's built-in memory tools from `agent.tools` without breaking startup. **0.5 day.**
3. **`auxiliary_client.py` local-vLLM spike** — point Hermes at `127.0.0.1:8000`, observe whether fallback chain interferes. **1 day.**
4. **lazy_deps.py audit** — inventory which plugins trigger runtime install; decide disable strategy. **0.5 day.**

Total spike budget: **2.5 days** before committing to the 11-18 day Module 7 effort. If spikes reveal a blocker we can't quickly route around, retreat to HYBRID-B (redaction-pipeline hook only, drop all of our memory/) — ~3 days total instead of 14.

## What this changes for the user-facing question

**Original question:** "зачем нам SQL база? вроде Гермес умеет набирать инфомрацию о пользователе и сохранять её"

**Honest answer (revised):** Hermes does have a memory system — flat-file MEMORY.md + USER.md with file-locking, drift detection, dedup, and a threat scanner. Its default is **functionally adequate** for our use case and has **stronger production guardrails** than what we wrote. The user was right to ask.

What our code still earns its place doing:
- **Redaction pipeline** that HALTS imports on verified secrets (Hermes redacts logs, doesn't abort persistence)
- **ChatGPT / Obsidian importers** (Hermes only migrates from sibling OpenClaw)
- **Prompt-content inference router** for our two-backend MoE + dense setup
- **Skill FTS5 search** (Hermes uses LLM-driven in-prompt scan; ours is a complementary tool the agent can call)
- **Day-0 wizard** that seeds USER.md + Facts
- **A `Fact` table with provenance + confidence** as Phase 2 infrastructure (recall@k upgrade target)

What we're walking away from in v2:
- `UserModel` table → use Hermes's USER.md instead
- Persona-lock enforcement → no real-world callers
- `update_user_model_field` → never called outside the wizard
- `delete_user_data(confirm=True)` → replace with `delete_fact_where(query)` matching Hermes's per-entry semantics
- `OpenAIChatClient` → Hermes's transports cover it
