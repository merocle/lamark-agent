# Audit — scenario 01: Local CLI coding session

**Verdict:** 🟡 Yellow — The scenario maps correctly to the plan, but five material gaps remain: (1) Edit-tool mtime/uniqueness contract not fully wired; (2) permission state retention across reuse underdocumented; (3) reducer determinism not explicitly tested; (4) cache-hit strategy on vLLM not pinned; (5) AGENTS.md vs CLAUDE.md precedence undefined. The first three are blockers for v0.1 exit; the last two are open ADR-level decisions.

---

## 1. Plan coverage matrix (filled)

| Concern | Covered in | Status | Evidence |
|---|---|---|---|
| `lamark chat` subcommand + TUI | plan/02 §"Subcommand surface", §"TUI region model" | ✅ | plan/02:27 defines `lamark chat`; plan/02:142–159 specifies three-pane ratatui TUI; 00d §14 locks region model |
| Layered config + bootstrap order | plan/03; plan/02 §"8-stage bootstrap" | ✅ | plan/03:10–21 specifies 8-source precedence; plan/02:95–125 shows bootstrap sequence; 00d §8 refines to explicit 8-stage order |
| Provider routing + cache breakpoints | plan/04 | ✅ | plan/04:246–274 defines CacheStrategy negotiation; plan/04:151–162 shows OpenAI-compat impl; plan/07:151–186 locks cache-control + prefix_hash strategies |
| SQ/EQ turn loop + Op/Event enums | plan/05 §"SQ/EQ event protocol", §"Turn loop" | ✅ | plan/05:36–76 defines Op/Event enums; plan/05:82–156 shows turn loop with inline hooks; plan/06:52–112 extends event taxonomy |
| Tool trait (`is_read_only`, `is_destructive`, `check_permissions`) | plan/05 + 00d §10, §11 | ✅ | 00d §10:10–45 specifies full Tool trait surface incl. `is_read_only`, `is_destructive`, `validate_input`, `check_permissions`; scenario step 13 relies on `is_read_only` gating |
| Edit/Write atomicity + mtime contract | plan/05 + 00c §20 | ⚠️ | 00c §20 references §05 but is truncated; scenario step 20 requires `old_string` uniqueness + mtime staleness check; **not found in plan/05** |
| Hook bus + UserPromptSubmit + PermissionRequest | plan/06 | ✅ | plan/06:29–113 defines hook bus + PermissionRequest event; plan/06:194–212 shows subprocess hooks; scenario steps 8, 17, 19 route through hook events |
| Permission policy DSL (Allow/Prompt/Forbidden) | plan/05; plan/06 | ✅ | plan/05:300–317 shows `policy.evaluate() → Decision`; 00d §29 locks `deny > ask > allow` aggregation; scenario steps 17–19 implement this flow |
| Trace bundle format + reducer | plan/06 §"Trace recorder", §"Reducer" | ✅ | plan/06:240–300 specifies bundle layout + manifest + trace.jsonl; plan/06:384–438 describes reducer output (conversation.jsonl); scenario step 24 invokes reducer |
| Memory recall + KB write | plan/07a | ✅ | plan/07a:28–108 defines MemoryProvider trait + KnowledgeBaseMemory impl; scenario steps 10, 25 read/write via KB client |
| Prompt cache (Anthropic + prefix) | plan/07 §"Cache" | ✅ | plan/07:141–200 specifies cache_control strategy + prefix_hash strategy; scenario step 9, 11 compose cached sections; step 29 shows cache-read tokens |
| Cost tracker | plan/02 + plan/11 | ⚠️ | plan/02:156 shows cost line in TUI; 00d §30 mentions cost-tracking shape but is truncated; scenario step 26 requires cost summary output; **per-turn persistence + resume not fully wired** |
| Offline KB fallback (outbox + SQLite mirror) | plan/07a §"offline fallback" | ⚠️ | plan/07a:121–150 shows KnowledgeBaseMemory.search() with timeout + fallback; scenario step 25 posts to KB; **fallback to outbox queuing not found** — plan/07a section truncated |

---

## 2. Self-improvement assertions

| Assertion | Plan support | Status | Evidence |
|---|---|---|---|
| **Trainer can build SFT samples.** Reducer produces ≥1 valid Nemotron-Agentic-v1 entry. | plan/06, plan/10 | ✅ | plan/06:411 — "conversation.jsonl (Nemotron-Agentic-v1 schema; one rollout = one line)"; plan/10:37 shows transform step; scenario step 24 invokes reducer |
| **Trainer can build DPO pairs on Reject.** User rejection yields (chosen, rejected) trajectory. | plan/06, plan/10 | ⚠️ | Scenario §18–19 describes Reject path; plan/06 records PermissionResolved event; **no explicit DPO-pair schema in plan**. plan/10 mentions "preference pairs from PermissionDenied events" but doesn't specify pair structure |
| **Memory recall on next session.** Fact written in session N is recalled in session N+1. | plan/07a | ✅ | plan/07a:38–51 defines MemoryProvider.search(); scenario steps 10, 25 test round-trip; KB HTTP client specified in plan/07a |
| **Cache hit on second turn.** Second turn shows non-zero cache_read_tokens. | plan/04, plan/07 | ✅ | plan/04:76 emits CacheReport; plan/07:196–198 aggregates cache_report; scenario step 29 checks `cache_read_tokens > 0` on second turn |
| **Reinforce signal is attached.** Bundle manifest contains `reinforce_signal` field. | plan/06, plan/07a | ⚠️ | Scenario expects `manifest.reinforce_signal = success/fail`; plan/06:262–277 shows manifest fields but `reinforce_signal` **not listed**; plan/07a mentions `reinforce()` method but no manifest authorship |
| **Reducer output is deterministic.** Two reductions yield byte-identical output. | plan/06 | ⚠️ | Scenario requires determinism; plan/06:411 states "no timestamps in conversation.jsonl" but **no explicit determinism test or algorithm spec**; must verify ID generation is content-addressed, not random |

---

## 3. Gaps surfaced

### G1. Edit-tool mtime staleness + old_string uniqueness contract
- **Owner file:** `plan/05-layer-4-agent-core.md`
- **Severity:** **Blocker**
- **Resolution:** Scenario step 20 requires Edit to reject if `old_string` is non-unique or file mtime is stale. Spec is only hinted at in addenda. Write a full §"Edit tool atomicity" subsection in plan/05 that specifies: (a) `old_string` must match exactly one contiguous substring; if ambiguous, return `Conflict` error; (b) mtime check before edit; if stale, return `Conflict` + re-Read hint; (c) atomic replace via tmp+rename on all platforms. Test: round-trip Edit with known mtime, then touch the file, retry Edit, assert Conflict.

### G2. Permission state retention across tool reuse within a session
- **Owner file:** `plan/05-layer-4-agent-core.md` or `plan/06-layer-5-hooks-trace.md`
- **Severity:** **Blocker**
- **Resolution:** Scenario step 19 ("Allow & remember for this path under this session") requires permission decisions to be persisted in-memory during the session so re-invocations on the same path skip the prompt. 00d §29 mentions `updatedPermissions` state but doesn't specify scope (path? tool+path? session?). Add a `SessionPermissionCache { key: (tool_name, arg_hash), decision: Decision, scope: PermissionScope }` to the Session state; document lookup order in PreToolUse hook evaluation.

### G3. Reducer determinism guarantee + algorithm spec
- **Owner file:** `plan/06-layer-5-hooks-trace.md`
- **Severity:** **Blocker**
- **Resolution:** Scenario assertion #6 requires byte-identical output on replayed reduction. Plan/06:411 claims "no timestamps in conversation.jsonl" but doesn't specify the full determinism contract. Must document: (a) all IDs are content-addressed (hash of input); no UUIDs; (b) no wall_time fields in reduced output; (c) event deduplication is idempotent; (d) tool-result payload references are sorted by ID. Add integration test: `reducer::tests::determinism_round_trip()`.

### G4. Cache strategy on vLLM: first-turn vs second-turn
- **Owner file:** `plan/04-layer-3-providers.md` or `plan/07-layer-6-prompt-and-cache.md`
- **Severity:** Deferred (scenario open question #1)
- **Resolution:** ADR to pin: (a) Is vLLM ≥ 0.10 with `prefix_cache_supported=true` a required dependency? (b) On first turn, do we expect `cache_write_tokens > 0` or only on second+? (c) Is `X-Cache-Hint` header sent only on turn 2+, or always? Document in plan/07 §"vLLM prefix cache behavior."

### G5. AGENTS.md vs CLAUDE.md vs LAMARK.md filename precedence
- **Owner file:** `plan/07-layer-6-prompt-and-cache.md`
- **Severity:** Deferred (scenario open question #4)
- **Resolution:** Plan/07 §"Tier-2 (Context, session-fixed)" line 89 says "AGENTS.md → walked up from cwd; first wins" but doesn't mention CLAUDE.md. Create an ADR: walk-up order `LAMARK.md > AGENTS.md > CLAUDE.md` (left-to-right wins), or is CLAUDE.md legacy-only (Claude Code compat)?

### G6. Reinforce signal in manifest + DPO-pair structure
- **Owner file:** `plan/06-layer-5-hooks-trace.md`
- **Severity:** **Blocker** (covers assertions #2 and #5)
- **Resolution:** Plan/06 manifest (lines 262–277) doesn't list `reinforce_signal`. Specify: (a) After TurnComplete, if all tool exits are success and user never Rejected, emit `reinforce_signal=success`; if any Reject or tool timeout, emit `fail`; (b) Store in manifest.json before KB POST; (c) For DPO, define explicit pair schema (chosen-trajectory vs rejected-trajectory) so the trainer can ingest it. Add manifest field documentation in plan/06 §"Manifest" and wire it in plan/05 §"Turn loop."

### G7. Cost tracker per-turn persistence and session resume
- **Owner file:** `plan/02-layer-1-entry-cli.md` or `plan/04-layer-3-providers.md`
- **Severity:** Deferred
- **Resolution:** Specify: (a) UsageStats accumulated in Session during turn loop; (b) checkpointed to manifest.json on TurnComplete; (c) on session resume, prior costs are loaded; totals shown include prior + current.

---

## 4. Open-question resolutions

| # | Question | Status |
|---|---|---|
| 1 | First-turn cache strategy on local vLLM? | **Still open** — plan/04 + plan/07 describe mechanics but don't commit to expected first-turn behavior. → ADR-004. |
| 2 | Where do per-path remembered permissions live? | **Still open** (partial evidence in 00d §29). → ADR-005. |
| 3 | Does the reducer run inline at TurnComplete or deferred? | **Answered** — plan/06:378: "On TurnEnded (status = SessionEnd) or every 30s while a session is long, run the reducer in-process." Inline at session end + every-30s checkpoint. |
| 4 | AGENTS.md vs CLAUDE.md vs LAMARK.md precedence? | **Still open** — plan/07:89 mentions AGENTS.md only. → ADR-006. |
| 5 | Trace bundle retention on disk? | **Answered** — plan/03 §"Trace" line 189–192: `rotate: { keep_days: 30, max_gb: 50 }`. Config-driven auto-prune. |

---

## 5. Recommended next-step ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-003 | Edit-tool atomicity contract | **Blocker** |
| ADR-004 | Session permission-state retention | **Blocker** |
| ADR-005 | Reducer determinism and testing | **Blocker** |
| ADR-006 | Reinforce-signal authorship in manifest + DPO-pair schema | **Blocker** |
| ADR-007 | vLLM cache strategy expectations | Deferred |
| ADR-008 | Context-file walk-up precedence (LAMARK.md / AGENTS.md / CLAUDE.md) | Deferred |
| ADR-009 | Cost tracker persistence + session resume | Deferred |
