# Audit — scenario 02: Permission-gated dangerous command

**Verdict:** 🟡 Yellow — DPO pair authoring (G-004 from scenario 01) still unresolved; policy-rule-proposal flow is invented entirely in the scenario with no plan owner; on-denial memory-write integration point absent. Hook aggregation + subprocess I/O are solid in `plan/00d`.

---

## 1. Plan coverage matrix (filled)

| Concern | Covered in | Status | Evidence |
|---|---|---|---|
| Permission policy DSL (Allow/Prompt/Forbidden) | plan/05 §"Approval flow" | ✅ | `Decision::Allow \| Forbidden \| Prompt` enum (plan/05:295–313); policy engine call at dispatch (plan/05:300) |
| `deny > ask > allow` aggregation across policy + hooks | 00d §29 + plan/06 | ✅ | 00d §29:876–885 defines aggregation; plan/06:152–186 implements via early-exit on `continue_: false` |
| Subprocess hook JSON I/O contract (stdin + stdout + exit-2) | 00d §2 | ✅ | 00d §2:116–175 — stdin/stdout format, exit-code-2 = deny, first-line async marker, prompt-response round-trip |
| PreToolUse / PermissionRequest / PermissionResolved / PermissionDenied events | plan/06 §"Hook event taxonomy" | ✅ | plan/06:69–113 enumerates events; plan/05:306–313 shows PermissionRequest emission; trace event variants plan/06:324–327 |
| Forbidden path skips modal entirely | plan/05 + 00d §29 | ✅ | plan/05:301–304 short-circuit; 00d §29:868 describes no modal for `Deny` |
| Structured tool-error on denial (not exception) | plan/05 §"Turn loop" | ✅ | Turn loop returns `ToolCallEnd { ok: false, error: PermissionDenied }`; expected by model |
| TUI permission modal with rationale + reversibility | plan/02 + 00d §14 | ✅ | 00d §14:500–515 — sticky header, modal overlay, permission dialogs |
| Memory fact write tagged by intent | plan/07a | ✅ | plan/07a:38–40 — memory trait with `metadata` field; supports tag-based storage |
| **Policy rule proposal flow (counter, threshold, append to policy.toml)** | (likely **gap**) | ⚠️ | Scenario §Step 5 invents the whole flow — counter, N=3 threshold, suggestion at session end, append to `.lamark/policy.toml`. **No plan file owns this.** |
| **DPO pair authoring in reducer** (G-004) | plan/06 + plan/10 | ❌ | plan/06 reducer (384–409) has no logic for projecting rejected branches. plan/10:166 names DPO but doesn't define rejected-trajectory schema. |
| Approval stickiness within a session (G-002) | plan/05 / 00d §29 | ⚠️ | 00d §29:880–884 adds `updated_permissions` to `Decision::Allow`. Cache **data structure** + lifecycle still unspecified. |
| Hook timeout = `ask`; exit-2 = `deny`; bad JSON = `ask` | 00d §3 | ✅ | 00d §3:170–174 — timeout → `ask`, exit-2 → `deny`, malformed JSON → `ask` |
| Permission prompt user timeout default | plan/03 config | ⚠️ | Scenario cites `permission_prompt_timeout` default 5 min. Plan/03 does not define this key. **Add to plan/03 config schema.** |
| Mock sandbox proof: denied call never reaches `sandbox.exec` | plan/05c | ✅ | plan/05c:42–64 trait def; denial happens in plan/05:300 *before* dispatch to plan/05:145–148 spawn |

---

## 2. Self-improvement assertions

| # | Assertion | Status | Evidence / why |
|---|---|---|---|
| 1 | **Denial trace round-trips to DPO sample.** Trainer ingests bundle, observes one DPO pair with `chosen.last_tool != rejected.last_tool` and identical prefixes. | ❌ | plan/10 names DPO (line 166, 380) but doesn't pin the rejected-trajectory schema. Scenario §Step 6 invents the shape. |
| 2 | **Memory recall on next session.** Agent's first proposal in same repo is `--force-with-lease`, not `--force`. | ⚠️ | plan/07a memory trait supports `search()` + `build_prompt_block()`, but **the on-`PermissionResolved` write hook is not specified anywhere**. |
| 3 | **Policy rule is durable.** After accepting `PolicyRuleProposal`, sessions 3 days later short-circuit as `Forbidden`. | ❌ | plan/05 has no "policy-rule proposal" subsystem at all — neither counter nor persistence. |
| 4 | **Hook decisions are traceable.** Trace contains `HookOutcome` event for every subscribed hook on `PreToolUse`. | ✅ | plan/06:342 — `HookCompleted { hook_id, outcome, duration_ms }` event exists. |
| 5 | **Forgetting-probe coverage.** `eval_set` includes a planted regression for "permission discipline." | ❌ | plan/10:64–73 defines eval categories but doesn't link to scenario-specific regression probes. Entangled with G-012. |

---

## 3. Gaps surfaced

### G-008. DPO pair authoring in reducer — **blocker**
- **Owner:** `plan/06-layer-5-hooks-trace.md` §"Reducer" + `plan/10-training-pipeline.md` §"DPO".
- **Resolution:** Define `dpo_rejected_trajectory.jsonl` and `dpo_chosen_trajectory.jsonl` schemas + the reducer rule that produces them from a single bundle containing `PermissionResolved { Deny }` followed by a successful re-plan. Pin: rejected branch includes only the **inference output up to and including the rejected tool call** + its policy/hook reason context; **no hypothetical tool result**. Chosen branch is the normal Nemotron-Agentic-v1 conversation from the next agent turn onward, with the same prefix.

### G-009. Policy-rule proposal subsystem — **deferred → blocker if we want closed-loop policy improvement**
- **Owner:** new `crates/lamark-policy/proposals.rs` referenced from `plan/05` §"Permission policy".
- **Resolution:** Specify (a) counter store (per-repo SQLite or KB-backed?), (b) hash signature for tool + arg-shape (must be stable across whitespace / refspec variants), (c) rolling-window threshold (default N=3, T=14d), (d) `PolicyRuleProposal` event type, (e) UX flow at `SessionEnd`, (f) writer semantics for `.lamark/policy.toml` (idempotent append + optional `git commit`).

### G-010. On-denial memory-write integration point — **blocker for self-improvement #2**
- **Owner:** `plan/07a-layer-6-memory-and-kb.md` + the agent turn loop in `plan/05`.
- **Resolution:** Specify that on `PermissionResolved { Deny, explanation }`, the agent loop calls `memory.write(MemoryFact { intent, scope, rationale, source_event })`. Must be **async + non-blocking** (KB outbox if offline); must not delay the next turn. Tag derivation: `intent` = LLM-extracted from the explanation in the next inference call's reasoning, or a deterministic regex fallback if inference fails.

### G-011. Session permission cache structure — **deferred**
- **Owner:** `plan/05` §"Permission policy" or new §"Session permission cache".
- **Resolution:** `HashMap<(ToolName, ArgHash), CachedDecision { decision, scope, expires_at }>`. Scope variants: `OnceThisTurn | ThisSession | ThisSessionAndPath`. Cleared on `SessionEnd`. Merge order in PreToolUse: cache hit short-circuits; otherwise run hooks. Visible in TUI side panel.

### G-012. DPO rejected-trajectory schema specifics — **blocker**
- **Owner:** `plan/10-training-pipeline.md` §"DPO data".
- **Resolution:** Companion to G-008. Concrete fields: `prompt_prefix`, `model_output_until_tool_call`, `proposed_tool_call`, `policy_reasons[]`, `hook_reasons[]`, `user_explanation`. No `tool_result`. JSON Lines schema must validate via a published JSON Schema in the repo.

### G-013. `permission_prompt_timeout` config key — **deferred**
- **Owner:** `plan/03-layer-2-config-bootstrap.md` §"Config schema".
- **Resolution:** Add `permissions.prompt_timeout_seconds = 300` to the canonical schema; on timeout treat as `Deny` with explanation `"timed out"`.

---

## 4. Cross-references to scenario 01's gap log

- **G-001** (Edit-tool atomicity) — not exercised by scenario 02.
- **G-002** (session permission state) — **re-surfaced and refined as G-011**.
- **G-003** (reducer determinism) — not exercised.
- **G-004** (`reinforce_signal` + DPO pairs) — **directly exercised; still open. Split into G-008 (reducer authoring) + G-012 (schema).**
- **G-005..G-007** (vLLM cache, walk-up precedence, cost tracker) — not exercised.

The DPO trail is now: **G-004 → G-008 + G-012**. The blocker is escalating, not shrinking — scenario 02 confirms scenario 01's intuition that this is the missing keystone for model-side self-improvement.

## 5. Open-question resolutions

| # | Scenario open question | Resolution |
|---|---|---|
| 1 | Counter window for policy-rule proposals (14d, 30d, all-time, per-repo vs global)? | **Still open.** → ADR-0010. Recommend per-repo rolling 14-day window, configurable. |
| 2 | Does `ask`-from-hook override `Allow`-from-policy? | **Answered in plan/00d §29.** Yes — `ask` wins; hook is the integrity boundary. |
| 3 | Where do `PolicyRuleProposal` UX strings live? | **Still open.** Recommend hardcoded for v0.1; localization deferred. |
| 4 | Can the agent itself propose a policy rule? | **Still open.** Recommend deferred — too risky for v0.1; only the compiler proposes. |
| 5 | DPO rejected-trajectory: projected vs executed? | **Partially answered by scenario itself.** Only tool call + reasoning, no hypothetical outputs. Schema pin needed → ADR-0009 / G-012. |

## 6. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-0009 | DPO pair schema + rejected-trajectory projection | **Blocker** |
| ADR-0010 | Policy-rule-proposal lifecycle (counter, threshold, write-back) | **Blocker** (for closed-loop policy) |
| ADR-0011 | Session permission cache and re-approval semantics | Deferred |
| ADR-0012 | On-denial memory-write integration point + intent extraction | **Blocker** (for self-improvement assertion #2) |
| ADR-0013 | `permission_prompt_timeout` and timeout-as-deny semantics | Deferred |
