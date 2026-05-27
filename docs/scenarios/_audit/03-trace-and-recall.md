# Audit — scenario 03: Trace bundle → KB upload → recall

**Verdict:** 🟡 Yellow — Core trace + memory architecture is solidly specified and coherent, but six gaps remain. The **decisive finding**: scenario 03 surfaces a real conflict between reducer determinism (required for training reproducibility) and memory-candidate extraction (which the scenario assumes happens post-reduce). If extraction is LLM-assisted, training reproducibility collapses. Resolution proposed below.

---

## 1. Plan coverage matrix (filled)

| Concern | Covered in | Status | Evidence |
|---|---|---|---|
| Trace bundle on-disk layout | plan/06:240–258 | ✅ | `~/.lamark/traces/<rollout_id>/{manifest.json, trace.jsonl, payloads/, reduced/, meta/}` |
| Manifest schema + `schema_version` | plan/06:262–277 | ✅ | `schema_version: "1"`; includes `agent_version`, `config_hash`, `kb_project_id` |
| Event variants enum + inline rule | plan/06:295–345 | ✅ | `TraceEvent` enum; ≤ 4 KB inline rule via `should_inline()` |
| Recorder API + O_APPEND durability | plan/06:347–368 | ✅ | `record()` flushes per-event with buffered writer; durable on EOF |
| Reducer in-process trigger | plan/06:378 | ✅ | "On TurnEnded (status = SessionEnd) or every 30s while a session is long" |
| KB upload + idempotency state | plan/06:378–380 | ✅ | `meta/kb_upload_state.json` stores last-seq-uploaded; restarts idempotent |
| Nemotron multi-turn rule (drop reasoning) | plan/06:411 + 00c §19 | ✅ | Reducer drops `reasoning_content` from non-last turns |
| Rotation / GC (`keep_days`, `max_gb`) | plan/06:415–417 + plan/03 | ✅ | `trace.rotate.keep_days=30, max_gb=50`; 1h background tick LRU |
| **Reducer determinism** (G-003) | 00c §19 references; **no ADR file** | ⚠️ | Cited as a requirement but neither plan/06 test list nor `docs/decisions/` carries the test or contract |
| **`reinforce_signal` in manifest** (G-004) | Scenario 03 invents; not in plan | ❌ | No plan author for manifest field or outcome-inference rules |
| **Memory candidate authoring** | Scenario 03 invents | ❌ | No plan owner for `lamark-memory::extract` / `lamark-trace::memorize`; see Critical Insight |
| MemoryProvider trait | plan/07a:30–51 | ✅ | `write`, `search`, `build_prompt_block`, `reinforce`, `health` |
| Lifecycle methods (`post_setup`, `sync_turn`, …) | 00c §4 | ✅ | Extended trait + `memory.skip_in_cron=true` default |
| `KnowledgeBaseMemory` + `SqliteSpool` | plan/07a:110–138 | ✅ | Fire-and-forget through spool; reads w/ timeout + SQLite fallback |
| `MemoryQuery` shape | plan/07a:64–71 | ✅ | `text`, `embedding`, `topic`, `kinds`, `time_range`, `limit_chars` |
| Tier-3 prompt block insertion | plan/07a:283–296 + plan/07 §"Tier-3" | ✅ | Inserted between `coordinator` and `custom` layers; cache_control ttl 1h |
| Cache cooperation when memory block changes mid-session | plan/07 §"Cache" | ⚠️ | Cache_control pinned; **mid-session invalidation behavior not explicit** |
| Reinforce on use | plan/07a:47, 252–279 | ✅ | `reinforce(id, ReinforceSignal)` trait method; reweights DPO samples |
| Quarantine on 4xx schema mismatch | Scenario 03 invents | ❌ | No plan owner; closest is plan/10 secrets-quarantine (unrelated) |
| Memory confidence threshold + drafts dir | Scenario 03 invents | ❌ | `memory.author.min_confidence` not in plan/03 config |
| Concurrent sessions in same project | Plan/06, plan/07a silent | ❌ | Write/read interleaving undefined |
| Disk-full → in-memory ring buffer | Scenario 03 invents | ❌ | No plan owner |

---

## 2. Self-improvement assertions

| # | Assertion | Status | Evidence / Gap |
|---|---|---|---|
| 1 | Memory loop closes round-trip (write T → read T+1) | ⚠️ | Architecture supports; blocked on G-014 |
| 2 | Trainer ingests `reduced/conversation.jsonl` directly | ✅ | plan/10:178–179 — connector reads `conversation.jsonl` lines + KB `GET /agents/{id}/traces` |
| 3 | Reinforce signal feeds back to ranking | ⚠️ | plan/07a:278 + plan/10:330–335; **who updates the KB rerank weight** is unspecified |
| 4 | Token reduction next session (≥ X%) | ⚠️ | Measurable, not provable until G-014 + G-004 close |
| 5 | Provenance survives reduction | ✅ | plan/07a:188 provenance tags; scenario 03 recalls with `source.session_id` |
| 6 | Determinism enables A/B replay | ⚠️ | Depends on G-003 |

---

## 3. Gaps surfaced

### G-014. Memory candidate authoring step — **blocker**
- **Owner:** new `lamark-memory::extract` (or `lamark-trace::memorize`); section in `plan/07a` or `plan/06`.
- **Resolution:** Define a deterministic post-reduce step that walks `reduced/state.json` + `reduced/conversation.jsonl` and emits `MemoryEntry` candidates (kinds: `Fact | Episode | UserProfile | Strategy`) tagged with `confidence`. **Must be heuristic-only in v0.1** (no LLM judge) so that the same bundle → same memory facts. See Critical Insight §6.

### G-015. 4xx schema-mismatch quarantine — **deferred**
- **Owner:** `plan/06` §"Knowledge-base upload" → new subsection "Error handling".
- **Resolution:** On KB 4xx, move bundle to `~/.lamark/traces/_quarantined/<rollout_id>/`; emit `Event::KbSchemaIncompatible`; `lamark doctor` lists quarantined bundles. Config `trace.quarantine_on_4xx` (default `true`).

### G-016. Memory confidence threshold + drafts directory — **deferred**
- **Owner:** `plan/03` config schema + `plan/07a` §"Candidate authoring".
- **Resolution:** Add `memory.author.min_confidence: 0.7`, `memory.author.draft_dir: ~/.lamark/memory/drafts`. Sub-threshold candidates land in drafts for Curator review (scenario 05).

### G-017. Concurrent sessions in same project — **deferred**
- **Owner:** `plan/07a` §"Offline mode" extension.
- **Resolution:** Memory writes tagged by `session_id`; KB deduplicates by `(session_id, turn_id, content_hash)`. Reinforce signals aggregate per memory id. Trace uploads use disjoint `rollout_id`. Integration test: two `lamark exec` in parallel; assert no duplicate facts.

### G-018. Disk-full ring-buffer fallback — **deferred**
- **Owner:** `plan/06` §"Recorder API" or `plan/11` (reliability).
- **Resolution:** v0.1 — pause + notify operator (TUI banner). v0.2 — bounded `VecDeque<TraceEvent>` (`trace.ring_buffer_events` = 1000), drop oldest, mark events `_lossy: true`.

### G-019. Cache invalidation when memory block changes mid-session — **deferred (likely no action)**
- **Owner:** `plan/07` §"Cache".
- **Resolution:** Document explicitly that Tier-3 changes invalidate the Tier-3 cache breakpoint only; Tier-1 stays cached. Net cost is tolerable because memory-write cadence is ≤ 1/turn. No mid-turn hook needed.

---

## 4. Cross-references to prior gaps

| Prior gap | Exercised by scenario 03? | Status after scenario 03 |
|---|---|---|
| G-001 (Edit-tool atomicity) | No | Still open (scenario 01). |
| G-002 / G-011 (session permission cache) | No | Still open. |
| **G-003 (reducer determinism)** | **Yes — directly.** Acceptance criterion: byte-identical re-reduction. | **Still open. Scenario 03 is the blocker that forces resolution.** |
| **G-004 (`reinforce_signal` in manifest)** | **Yes — directly.** Acceptance criterion: manifest field populated. | **Still open. Scenario 03 demands `reducer::infer_outcome` exist.** |
| G-005..G-007 (vLLM cache, walk-up, cost tracker) | No | Still open. |
| G-008 / G-012 (DPO pair / rejected-trajectory schema) | No (denial scenarios are #02 / #10) | Still open. |
| G-009 (policy-rule proposal) | No | Still open. |
| G-010 (on-denial memory write) | Indirectly — scenario 03 assumes the memory-write integration point exists | Still open; this scenario shows where it must plug in. |
| G-013 (`permission_prompt_timeout`) | No | Still open. |

**Net:** Scenario 03 does **not** introduce new dependencies on G-001/G-002/G-007 — it's self-contained on the trace/memory axis. It tightens the urgency of G-003 and G-004 because their absence breaks the acceptance criteria.

## 5. Open-question resolutions

| # | Scenario 03 OQ | Resolution |
|---|---|---|
| 1 | Reducer or dedicated extraction step? | **Dedicated step, deterministic in v0.1.** See Critical Insight + G-014. LLM-assisted curation belongs in Curator (scenario 05). |
| 2 | Should reduced bundles be retrievable as Tier-3 context? | **Defer to v0.2.** Config `memory.recall_trajectories: false` default. |
| 3 | `memory.tier3_budget_chars` global or per-project? | **Global default with per-project override via `AGENTS.md` frontmatter.** Pin in plan/03 + plan/07. |
| 4 | Single `POST /memory/search` vs typed endpoints? | **Single endpoint.** KB blends internally. Already aligned with plan/07a:143–155. |
| 5 | Reinforce-on-use detection mechanism? | **v0.1: substring match between recalled fact content and final-turn reasoning / tool-call args.** Conservative threshold (e.g., 0.85). v0.2: tool-path matching. Pin in plan/07a new §"Reinforce signal scoring". |
| 6 | Manifest schema-version migration story? | **Trainer accepts both v1 and v2.** Reducer can target either via config. Backward-compat note in plan/10. |

## 6. Critical insight — determinism vs memory-extraction

Scenario 03 simultaneously demands:

1. **Reducer determinism** (acceptance criterion: byte-identical re-reduction).
2. **Memory candidate authoring** as a step that runs over the reduced bundle (scenario steps 4 + 11).

If memory authoring is LLM-assisted (e.g., "ask a frontier model which tool outputs are general-purpose facts vs session-specific"), then **two trainers starting from the same bundle ingest different memory facts**, and the reproducibility promise collapses. Every A/B comparison of model adapters becomes contaminated by extraction noise.

**Resolution.** Memory authoring in v0.1 is **purely heuristic** and **deterministic by construction**:

- Tool calls that returned `ok: true` with structured output → candidate `Fact`.
- Multi-turn sequences ending with `PermissionResolved { Deny } → re-plan → success` → candidate `Episode`.
- Successful agent strategies (consistent prompt → outcome pairs) → candidate `Strategy`.
- Confidence is a deterministic function of trace structure (result type, error count, outcome class). No frontier-model call.

Reflexion-style LLM curation of memory belongs in **scenario 05 (Curator)**, which is explicitly a non-deterministic filter operating over a deterministic candidate stream. This separation cleanly preserves: (a) trainer reproducibility, (b) the agent's ability to grow durable knowledge over time.

This insight should be promoted to an ADR before scenario 03's acceptance test runs.

## 7. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-005 | Reducer determinism contract + CI test | **Blocker** (closes G-003) |
| ADR-006 | Outcome-inference rules for `manifest.reinforce_signal` | **Blocker** (closes G-004) |
| ADR-0014 | Heuristic-only memory extraction in v0.1 (no LLM in reducer-adjacent path) | **Blocker** (closes G-014; protects training reproducibility) |
| ADR-0015 | 4xx-quarantine workflow + operator UX | Deferred (G-015) |
| ADR-0016 | Memory confidence threshold + drafts pipeline | Deferred (G-016) |
| ADR-0017 | Concurrent-session interleaving guarantees | Deferred (G-017) |
| ADR-0018 | Disk-full lossy ring-buffer (v0.2) | Deferred (G-018) |
| ADR-0019 | Tier-3 cache invalidation policy mid-session | Deferred (G-019) |
