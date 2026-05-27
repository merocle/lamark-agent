# 03 — Trace bundle → KB upload → recall next session

> **Phase:** P3 → P4 (`plan/00-overview.md`).
> **One-liner:** A session ends; its trace bundle reduces to
> `conversation.jsonl`, syncs to knowledge-base, and seeds memory facts;
> the **next** session in the same project recalls those facts (and the
> reduced trajectory) into its prompt, demonstrating one full loop of
> Lamark's persistence promise.

---

## North-star contribution

This scenario is the **seam** between the two self-improvement loops:

- **Agent-side self-improvement.** The fact written this session is what
  next session retrieves. If this pipeline isn't tight, every other "agent
  side" claim collapses: skills can't be authored from past trajectories,
  Reflexion can't critique, Curator can't consolidate, OPRO has nothing to
  optimize. Scenario 03 *is* the agent-side memory loop.
- **Model-side self-improvement.** The reduced bundle in
  `reduced/conversation.jsonl` (Nemotron-Agentic-v1) is the **single
  trainer input**. Scenario 03 proves that file actually appears, validates
  against the schema, gets uploaded to `POST /agents/{id}/traces`, and is
  durable across crashes (idempotent re-upload via
  `meta/kb_upload_state.json` per `plan/06:378–380`).

### Quality levers in scope

- **Determinism of the reducer** (G-003) — without it, training is
  non-reproducible.
- **Offline tolerance** — KB down ≠ session lost; spool + outbox
  (`plan/07a §"local write spool"`).
- **Tier-3 prompt block** — recall doesn't just exist, it appears in the
  agent layer of the next session's prompt *with provenance* so the agent
  can reason about its sources (`plan/07a §"build_prompt_block"`).
- **Cache cooperation** — the recalled block lives in a section that the
  prompt composer marks cacheable when stable (`plan/07 §"Cache"`).

### Signals produced / consumed

- **Produces (this session):**
  - `~/.lamark/traces/<rollout_id>/trace.jsonl` — raw append-only.
  - `~/.lamark/traces/<rollout_id>/reduced/conversation.jsonl` — trainer-ready.
  - `~/.lamark/traces/<rollout_id>/reduced/state.json` — graph for forensics.
  - `~/.lamark/traces/<rollout_id>/meta/kb_upload_state.json` — durability marker.
  - `POST /agents/{id}/traces` — server-side persistence + indexing.
  - `POST /memory/facts` — N facts authored from this session.
- **Consumes (next session):**
  - `GET /memory/search?q=…` (semantic + topic) — recall facts.
  - `GET /agents/{id}/traces?recent=N&project=…` — recall reduced
    trajectories for "what did we try last time?" context (optional /
    feature-flagged; very tokens-heavy).

---

## Idea

Anna closes the chat from scenario 01. Tomorrow she opens a new `lamark
chat` in the same repo. Before she's typed anything, the prompt composer
has already injected: *"retry helper lives in `crates/foo/src/retry.rs`
(authored 2026-05-24)"* into the memory block. She types *"add an
exponential-backoff variant"* and the agent goes straight to that file
instead of re-discovering it. Tomorrow's session is **N tool calls
shorter** than today's because of yesterday's memory writes — that delta
is the agent-side ROI.

## Actors

| Actor | Role |
|---|---|
| **Anna** | Same as scenario 01. Comes back to the same project the next day. |
| **Trace recorder** | `crates/lamark-trace/` — writes `trace.jsonl` per `plan/06 §"Recorder API"`. |
| **Reducer** | `lamark-trace::reducer::reduce` — runs in-process on `TurnEnded { SessionEnd }` or every 30s (`plan/06:378`). |
| **KB client** | `crates/lamark-kb-client/` — POSTs reduced bundle + memory facts (`plan/07a`). |
| **SqliteSpool** | Local fallback when KB is unreachable (`plan/07a §"local write spool"`). |
| **Memory provider** | `KnowledgeBaseMemory` (default), with `sqlite::SqliteMemory` fallback (`plan/07a §"Implementations"`). |
| **Prompt composer** | `crates/lamark-prompt/` — invokes `memory.build_prompt_block(ctx)` at Tier-3 (`plan/07 §"Tier-3 volatile"`). |
| **`knowledge-base`** | Kotlin/Spring service. Indexes `/memory` with hybrid retrieval (dense + sparse + KG + RAPTOR). |

## Trigger

Two triggers — this scenario covers **both halves** because they're
worthless apart:

- **Write half** (end of session N):
  - User types `/quit` or sends SIGINT; OR
  - Last `TurnEnded { status: SessionEnd }` event.
- **Read half** (start of session N+1):
  - `SessionStart` hook fires; before the first model call, prompt
    composer runs through Tier-1 → Tier-3; the Tier-3 memory block calls
    `memory.search(MemoryQuery { topic: project_topic, … }, k=…)`.

## Pipeline

### Write half — end of session N

1. **Hook bus** emits `SessionEnded`. Recorder's subscriber (priority 999 — runs last per `plan/06 §"Hook subscription"`) flushes the buffered writer and seals `trace.jsonl`.
2. **Reducer runs in-process.** Reads `trace.jsonl` deterministically (`plan/06 §"Reducer"`):
   - All event IDs must already be content-addressed (G-003 / ADR-005).
   - `reasoning_content` from non-last turns is dropped per Nemotron-Agentic-v1 multi-turn splitting rule (`plan/06:411`).
   - Outputs `reduced/state.json` (graph) + `reduced/conversation.jsonl` (Nemotron-Agentic-v1, one line per rollout).
3. **Outcome inference.** `reducer::infer_outcome(&state)` classifies the session as `success | fail | mixed | unknown` based on:
   - Did all tool calls end `ok: true`?
   - Were there `PermissionDenied` events without subsequent recovery?
   - Did the user end the session with a satisfaction signal (configurable; e.g., `/done` slash command)?
   - This populates the new `manifest.reinforce_signal` field (G-004 / ADR-006).
4. **Memory authoring.** Reducer (or a small post-reduce step) walks the reduced conversation and emits `MemoryEntry { kind, content, topic, source, session_id, turn_id }` candidates:
   - `Fact` from confirmed tool outputs ("file X exists at path P", "test Y passes")
   - `Episode` from completed multi-step recoveries ("when X failed because Y, doing Z fixed it")
   - `Strategy` from successful prompt → outcome pairs (later consumed by `plan/07b` Reflexion/OPRO)
   - Each candidate is **tagged** with `project_id` + `repo_root_hash` + a `confidence` (`0..1`) so retrieval can rerank.
5. **KB upload, two POSTs:**
   - `POST /agents/{agent_id}/traces` body `{ manifest, reduced_state, conversation }` (`plan/06:378`).
   - For each memory candidate above confidence threshold, `POST /memory/facts` via `KnowledgeBaseMemory::write` (`plan/07a §"KnowledgeBaseMemory"`).
6. **Durability.** Each successful upload bumps `meta/kb_upload_state.json` (last-seq-uploaded for the bundle, last-fact-id-flushed for memory). Restarts are idempotent. If KB returns 5xx, the SqliteSpool keeps the writes and retries on next `MemoryProvider::sync_turn` or on a 60s background tick.
7. **TUI exits.** Cost summary already printed (scenario 01 step 26). A `lamark doctor` after exit reports `bundles_pending_upload: 0` (or N, with a hint).

### Read half — start of session N+1

8. **Bootstrap (cold).** Plan/02 8-stage bootstrap. Provider router probes vLLM. `SessionStart` hook fires.
9. **AGENTS.md walk-up** loads project context. Static.
10. **Prompt composer Tier-3 build.** For each registered memory provider, composer calls `provider.build_prompt_block(ctx)` with:
    - `ctx.topic = project_topic` (derived from cwd repo identity)
    - `ctx.session_recent_intents = []` (empty at session start)
    - `ctx.budget_chars = config.memory.tier3_budget_chars` (default e.g. 2000)
11. **`KnowledgeBaseMemory::build_prompt_block`** internally:
    - Issues `POST /memory/search` with the project topic + capability flags.
    - KB returns ranked recalls with provenance tags (`kb:vector`, `kb:graph`, `kb:fts`, …).
    - The provider formats them into a compact bulleted block with provenance comments:
      ```
      <!-- memory: kb:vector score=0.91 src=session 01HZ… 2026-05-24 -->
      • retry helper lives in `crates/foo/src/retry.rs`
      ```
12. **Block is inserted into the agent layer** of the layered system prompt (between `coordinator` and `custom`, per `plan/07 §"layered system-prompt composition"`).
13. **Cache decision.** The block is stable for the session as long as no new memory facts land mid-session, so it sits inside the *cached* prefix on Anthropic-compat providers (`cache_control: ephemeral` breakpoint). On vLLM, prefix-cache friendliness is preserved by **not** interleaving freshly-recalled lines into the middle of the prompt mid-session (`plan/07 §"Cache"`, ADR-007).
14. **First inference.** Anna types her message. The model reads the memory block + AGENTS.md + her prompt. Its first tool call is `Read("crates/foo/src/retry.rs")` — direct, no Grep step.
15. **Reinforcement on success.** If the session finishes successfully and Anna doesn't undo any change, the agent loop calls `memory.reinforce(memory_id, ReinforceSignal::Used { useful: true })` on every recalled entry that was actually surfaced in the prompt. KB updates the rerank weight.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1 (recorder flush) | `lamark-trace::Recorder` | 06 §"Recorder API" |
| 2 (reducer) | `lamark-trace::reducer` | 06 §"Reducer" |
| 3 (outcome → reinforce_signal) | `lamark-trace::reducer::infer_outcome` (new submodule) | 06 + G-004 |
| 4 (memory candidate authoring) | new: `lamark-trace::memorize` or `lamark-memory::extract` | (likely **gap**) |
| 5 (KB POSTs) | `lamark-kb-client` | 07a |
| 6 (durability + spool) | `lamark-trace::meta`, `lamark-memory::sqlite_spool` | 06 + 07a |
| 8–9 (bootstrap, AGENTS.md) | `lamark`, `lamark-prompt` | 02, 07 |
| 10–12 (Tier-3 build, block insertion) | `lamark-memory::compose`, `lamark-prompt` | 07a §"prompt_block.rs", 07 §"Tier-3 volatile" |
| 13 (cache decision) | `lamark-prompt`, `lamark-providers` | 07 §"Cache", 04 |
| 15 (reinforce) | `lamark-memory` | 07a §"MemoryProvider::reinforce" |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Process killed mid-reduction** | `trace.jsonl` is durable (O_APPEND + flush per event, `plan/06:368`). On next `lamark doctor` / next session start, the reducer reruns over the same bundle; output is identical by determinism (G-003). |
| **KB returns 5xx** | Spool keeps trace + memory writes. Retry on 60s background tick. `lamark doctor` reports `bundles_pending_upload`. |
| **KB returns 4xx (schema mismatch)** | **Hard fail visible to operator.** A 4xx means our reduced shape doesn't match KB's contract — typically a `schema_version` drift. Bundle is quarantined into `~/.lamark/traces/_quarantined/<rollout_id>/`; operator notified. |
| **Memory fact authoring extracts garbage** | A confidence threshold (`memory.author.min_confidence`, default 0.7) filters; sub-threshold candidates are written into `~/.lamark/memory/drafts/` for Curator review (scenario 05). |
| **KB hot but slow at session start** | `memory.search` has a 1.5s budget (config: `memory.tier3_timeout_ms`). On timeout, prompt composer falls through to `SqliteMemory` for that turn; logged + emitted as `Notification` event. |
| **Embedding service down** | KB indexes lazily on its side; client doesn't care. Cold-start recall may use sparse-only ranking; KB indicates `provenance: kb:fts` instead of `kb:vector`. |
| **Reducer non-determinism leaks** | Caught by `reducer::tests::determinism_round_trip()` (G-003). If asserted in CI, scenario 03's training contract is safe. |
| **Disk full during recorder flush** | Hook bus emits `Notification { level: Error }`; turn loop pauses with a TUI banner. Trace recorder switches to in-memory ring buffer until disk pressure clears (best-effort, lossy — explicit trade). |
| **Two Lamark sessions in the same project concurrently** | Each gets its own `rollout_id`; memory writes are tagged by session; reads see facts from both. No conflict; reinforce signal aggregated per-fact. |

## Acceptance criteria

- [ ] Closing a session leaves `~/.lamark/traces/<id>/{trace.jsonl, reduced/conversation.jsonl, reduced/state.json, meta/kb_upload_state.json}` all populated.
- [ ] `reduced/conversation.jsonl` validates against the published Nemotron-Agentic-v1 JSON Schema (in-repo).
- [ ] Running `lamark trace reduce <id>` twice on the same bundle produces byte-identical output (G-003).
- [ ] `manifest.reinforce_signal` is one of `success | fail | mixed | unknown` (G-004) and reflects the actual session outcome.
- [ ] `POST /agents/{id}/traces` and N × `POST /memory/facts` are observed on a stub KB; payloads validate against the KB API contract (`../knowledge-base/docs/07b-public-api-rfc.md`).
- [ ] Killing `lamark` with `SIGKILL` after the recorder flushes but before KB upload — restarting reuploads idempotently; no duplicate KB rows.
- [ ] Next-session start: `memory.build_prompt_block(...)` returns ≥ 1 recall whose `source.session_id` matches the prior rollout, within the configured budget.
- [ ] The recalled block appears in the agent prompt layer with a provenance comment per recall.
- [ ] On Anthropic-compat provider, second turn of session N+1 shows non-zero `cache_read_tokens` despite the memory block being newly inserted at session start.
- [ ] Reinforce call fires at session end when a recalled fact was used; KB receives `POST /memory/{id}/reinforce` or equivalent.

## Self-improvement assertions

1. **Memory loop closes round-trip.** Same project, same topic, write at T → read at T+1 in a different process. Asserted via end-to-end integration test that runs two `lamark exec` scripts in sequence and observes the recall.
2. **Trainer can ingest yesterday's bundle.** Running the training pipeline (`plan/10`) against `reduced/conversation.jsonl` from this scenario produces ≥ 1 valid SFT sample without further transformation.
3. **Reinforce signal feeds back into ranking.** After `memory.reinforce(id, Used { useful: true })` fires N times for a given fact, that fact's KB rank increases — verifiable via `POST /memory/search` returning it earlier in the result set.
4. **Token reduction next session.** Holding the user prompt and task constant, a second session in the same project completes the same task with ≥ X% fewer tool calls than the first (X measured, not asserted; expected ≥ 20% on the retry-helper task).
5. **Provenance survives reduction.** Every recalled line in the next session's prompt carries provenance metadata; an audit script can trace each line back to a `rollout_id` + `turn_id` + `seq`.
6. **Determinism enables A/B.** Because the reducer is deterministic, we can later replay a bundle through a candidate trainer and compare adapters; without determinism, every comparison is contaminated by reduction noise (this assertion belongs to scenario 03 even though it's used by scenario 15).

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Trace bundle on-disk layout | plan/06:240–258 §"File layout on disk" | _audit_ |
| Manifest schema + `schema_version` | plan/06:262–277 | _audit_ |
| Event variants enum + `payload_ref` inline rule | plan/06:295–345 | _audit_ |
| Recorder API + `O_APPEND` durability | plan/06:347–368 | _audit_ |
| Reducer in-process trigger (SessionEnd + 30s) | plan/06:378 | _audit_ |
| KB upload + idempotency (`kb_upload_state.json`) | plan/06:378–380 | _audit_ |
| Nemotron-Agentic-v1 multi-turn rule (drop `reasoning_content` from prior turns) | plan/06:411 | _audit_ |
| Rotation / GC (`keep_days`, `max_gb`) | plan/06:415–417 + plan/03 | _audit_ |
| **Reducer determinism** (G-003 / ADR-005) | (gap; required) | _audit_ |
| **`reinforce_signal` authorship in manifest** (G-004 / ADR-006) | (gap; required) | _audit_ |
| **Memory candidate authoring from reduced bundle** | (likely **gap**; new submodule) | _audit_ |
| MemoryProvider trait + `build_prompt_block` | plan/07a:30–80 | _audit_ |
| `KnowledgeBaseMemory` + SqliteSpool fallback | plan/07a:110–120 + §"local write spool" | _audit_ |
| `MemoryQuery` topic/time-range/kinds | plan/07a:64–71 | _audit_ |
| Tier-3 prompt block insertion + cache cooperation | plan/07 §"Tier-3 volatile", §"Cache" | _audit_ |
| Reinforce on use (success signal flows back to KB) | plan/07a:47, MemoryProvider::reinforce | _audit_ |
| Quarantine on 4xx schema mismatch | (likely **gap**) | _audit_ |
| Memory fact confidence threshold + drafts dir | (likely **gap**) | _audit_ |
| Concurrent sessions in same project | plan/06 + plan/07a (semantics unclear) | _audit_ |
| Disk-full fallback to in-memory ring buffer | (likely **gap**) | _audit_ |

## Open questions

1. **Who authors memory facts — reducer or a dedicated step?** Scenario assumes the reducer or a sibling `memorize` step that runs immediately after. If it lives in the reducer, determinism becomes a much harder property (memory extraction may be LLM-assisted → non-deterministic). Likely answer: **deterministic reducer + separate non-deterministic memorize step**. Pin in plan/06 or new section.
2. **Should reduced bundles also be retrievable as Tier-3 context?** I.e., not just facts, but "here's last session's reduced trajectory." High signal but heavy in tokens. Feature-flag default off; v0.1 ships off.
3. **`memory.tier3_budget_chars` — global or per-project?** Project-specific is desirable but adds config sprawl. Default global with per-project override.
4. **Hybrid retrieval surface.** Does the Lamark client send a single `POST /memory/search` and let KB blend dense+sparse+KG+RAPTOR, or does it call typed endpoints separately? Affects KB API surface (`../knowledge-base/docs/07b-public-api-rfc.md`).
5. **Reinforce-on-use detection.** How do we *know* a recalled fact was used? Inspect the model's reasoning content for substring match? Compare tool calls to fact-implied paths? Conservative heuristic + explicit logging is fine for v0.1; ADR.
6. **What happens to `reduced/conversation.jsonl` if the manifest schema bumps to v2?** Migration story: do we re-reduce old bundles, or version the schema and let the trainer accept multiple?
