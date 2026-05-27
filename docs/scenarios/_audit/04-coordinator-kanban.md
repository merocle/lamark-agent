# Audit — scenario 04: Multi-agent Kanban + /goal Ralph loop

**Verdict:** 🟡 Yellow — execution leg is well-specified (Kanban tools, spawn, heartbeat, zombie detection, Ralph iteration, KB mirror, sandbox egress all map cleanly to `plan/05a` and `plan/05c`). Training leg is incomplete: three blockers — G-020 (tree-level `reinforce_signal` aggregation), G-021 (counterfactual spawn-N DPO), G-022 (aggregate-statistic memory) — break the self-improvement closure at multi-agent scale.

---

## 1. Plan coverage matrix (filled)

| Concern | Covered in | Status | Evidence |
|---|---|---|---|
| Coordinator + Kanban roles | plan/05a §"Roles", §"Kanban board" | ✅ | `KanbanBoard` struct + card state machine; Coordinator lifecycle (plan/05a:24–99) |
| Card state machine + tools | plan/05a §"Kanban tools" | ✅ | Nine tools + state diagram (plan/05a:101–118); `Card` with `blocked_by/blocks` (plan/05a:62–77) |
| `spawn_agent` / `followup_task` / `close_agent` | plan/05a §"Coordinator tools" | ✅ | plan/05a:121–131 |
| Per-subagent isolation (conversation / tools / sandbox / memory tags) | plan/05a §"Subagent isolation" | ✅ | Six isolation guarantees; disjoint trace bundles (plan/05a:44–56) |
| Heartbeat + zombie detection algorithm | plan/05a §"Heartbeat + zombie detection" | ✅ | Four-step zombie response + 10s ticker (plan/05a:148–157) |
| Cascading cancel + budget enforcement | plan/05a config + plan/05c §"Budget enforcement" | ⚠️ | Budget struct exists (plan/05c:386–392); cancel SLA (≤ 3s) not pinned anywhere — see G-024 |
| Ralph loop + workspace lock | plan/05a §"Ralph /goal loop" | ✅ | Pseudocode + stale-PID handling (plan/05a:159–210) |
| `interaction_edges` in `state.json` | plan/05a §"Trace integration" | ✅ | Four edge kinds (`spawn`, `claim`, `result`, `message`) (plan/05a:262–269) |
| KB Kanban mirror | plan/05a §"Knowledge-base mirror" | ✅ | `POST /agents/{coordinator_session_id}/kanban/{card_id}/events` (plan/05a:273–279) |
| Subagent trace bundle independence (own rollout_id) | plan/06 §"Trace integration" + plan/05a:54 | ✅ | Child gets own `rollout_id`; parent records `SubagentSpawned { child_rollout_id }` |
| **`reinforce_signal` aggregation across tree** (G-020) | (no plan owner) | ❌ | Scenario explicitly flags as ADR-pending; not addressed in plan/05a or plan/07a |
| **Counterfactual DPO replay for spawn-N decisions** (G-021) | (no plan owner) | ❌ | No schema, no reducer logic, no trainer contract |
| **Memory facts as aggregate stats** (G-022) | plan/07a `MemoryKind` (54) | ⚠️ | Existing variants: `Fact, Episode, UserProfile, Skill, Strategy`. No `Statistic` |
| Sibling-message routing + policy | plan/05a §"Inter-agent messaging" | ✅ | `send_message` + `policy.evaluate_sibling_msg` (plan/05a:238–255) |
| Slack post via gateway (poster role) | plan/09 §"Part A — Gateway" | ✅ | Slack adapter (plan/09:95–139) |
| Skill draft emission from convergent subagent patterns | plan/08 §"Curator" | ⚠️ | Curator archives/consolidates but doesn't *emit* skill drafts from convergence; no similarity metric — see G-023 |
| Max-spawn-depth + max-concurrent-subagents enforcement | plan/05a §"Configuration" | ✅ | Keys present (plan/05a:214–220); rejection on depth exceed (plan/05a:332) |
| TUI Kanban panel | plan/02 + plan/05b | ⚠️ | plan/05b:250 references Tasks/Kanban toggle; full panel spec not located in plan/02 |
| Per-subagent egress policy (network namespaces) | plan/05c §"Egress policy" | ✅ | Three modes (`None`, `ModelProviderOnly`, `Allowlist`); Docker sidecar + K8s NetworkPolicy (plan/05c:193–317) |
| `WorkspaceLock` filesystem + stale-PID detection | plan/05a:174–210 | ✅ | Take-over with warning on stale PID |

---

## 2. Self-improvement assertions

| # | Assertion | Status | Why |
|---|---|---|---|
| 1 | Per-subagent traces are SFT-quality (< 1/N tokens of monolith) | ✅ (with caveat) | Independent rollouts per plan/05a:54; measurement metric not yet in CI |
| 2 | `interaction_edges` enable orchestration-DPO | ⚠️ | Edges are *emitted*; **DPO pair construction schema missing** — G-021 |
| 3 | Zombie detection produces high-value negative samples | ✅ | Trace sequence well-defined; no explicit "negative-sample" labeling contract for trainer (minor) |
| 4 | Coordinator decision rate + aggregate memory facts | ❌ | `MemoryKind::Statistic` doesn't exist — G-022 |
| 5 | Skill emergence from convergent subagent patterns | ⚠️ | Curator detects patterns but similarity metric unspecified — G-023 |
| 6 | Reinforce-aggregation transparency (per-child breakdown) | ❌ | No tree-level reinforce schema — G-020 |

---

## 3. Gaps surfaced

### G-020. Tree-level `reinforce_signal` aggregation — **blocker**
- **Owner:** `plan/05a` §"Ralph /goal loop" + `plan/07a`.
- **Resolution:** Pin the rule: (a) identify critical-path children via card-dependency traversal, (b) AND across critical path with user-veto override (no `/undo`, no `/stop`), (c) store in `TraceEvent::RalphLoopComplete { aggregated_reinforce_signal, per_child: [...] }` and surface to `manifest.reinforce_signal` for the parent bundle. CI test: scenario 04 with a planted child failure → parent signal = `mixed`; with all children OK → `success`.

### G-021. Counterfactual DPO for orchestration decisions — **blocker**
- **Owner:** `plan/06` reducer + `plan/10` training pipeline.
- **Resolution:** Companion to G-008 / G-012 at coordinator granularity. Define `CounterfactualInteractionEdge` variants — projected from observed failures (e.g., "had we spawned N=1 instead of N=4, cards 2–30 would have timed out under budget"). Reducer constructs `(chosen, rejected)` pairs from `interaction_edges`. Trainer ingests as DPO over orchestration policy. **Likely sub-ADR splits:** projection algebra; admissibility (which counterfactuals are well-grounded vs speculation); weighting.

### G-022. Aggregate-statistic memory kind — **deferred**
- **Owner:** `plan/07a` (`MemoryKind` enum).
- **Resolution:** Two-stage. **v0.1:** allow stats as `MemoryKind::Fact` with `#stat:` prefix and structured metadata (`numerator`, `denominator`, `basis`). **v0.2:** promote to `MemoryKind::Statistic { topic, n, k, basis, ci_low, ci_high }`. Extraction step (G-014) gains an aggregate-pass that walks N trace bundles tagged by the same project/topic.

### G-023. Skill convergence-detection metric — **deferred**
- **Owner:** `plan/08` §"Curator".
- **Resolution:** Pin to **counter-only** in v0.1 ("the same `(tool_name, normalized_args_shape)` sequence appears in ≥ 3 independent subagent bundles within W days"). Levenshtein / embedding similarity deferred to v0.2 after we see false-positive rates. CI test: run scenario 04 three times; on the third run, a skill draft appears in `~/.lamark/skills/drafts/`.

### G-024. Cascading-cancel timing SLA — **deferred**
- **Owner:** `plan/05a` §"Configuration" + `plan/05c` interrupt budget.
- **Resolution:** Add `coordinator.cancel_deadline_seconds = 2`. Cancel cascade: coordinator broadcasts `Submission::Interrupt` → each child honors its cancel token → after deadline, `AgentHandle::kill()`. Note that `plan/05c:391` currently sets `interrupt_grace = 5s`, which is *longer* than the scenario's 3s SLA — needs reconciliation (probably split `child_cancel_deadline` from `tool_interrupt_grace`).

### G-025. Kanban board durability on coordinator crash — **deferred**
- **Owner:** `plan/05a` §"Kanban board" + §"Knowledge-base mirror".
- **Resolution:** Persist board to `~/.lamark/state/coordinator-<rollout_id>/board.json` on every mutation (durable + fast). On coordinator restart, load from file (or KB if file gone). v0.1 acceptable to accept loss between KB sync intervals; v0.1-extended adds the file.

### G-026. Per-subagent tool-proxy mode selection — **deferred**
- **Owner:** `plan/05c` §"Tool proxy modes" + scenario 04 `AgentSpec` examples.
- **Resolution:** Pin per-role recommendations in plan/05d worked configs: poster = `Restricted` allowlist + `ProxyToParent` for Slack auth; scraper = `Restricted` with `WebFetch` direct; summarizer = `Restricted` with `Read` direct only. CI test: poster attempts `WebFetch` → denied; scraper attempts Slack → denied.

---

## 4. Cross-references to prior gaps

- **G-003** (reducer determinism) — Now tested at scale: the reducer must produce identical `interaction_edges` graphs on replay. Determinism contract from scenario 03 carries forward — content-addressed edge IDs, no wall-time.
- **G-004 / G-020** — G-004 was "single manifest field"; G-020 extends to *tree* aggregation. Both must close together for parent-trace signal to be trustworthy.
- **G-008 / G-012 / G-021** — Same family: counterfactual trajectories. Now needed at three granularities (tool call, agent decision, orchestration decision). One umbrella ADR could cover all three.
- **G-010** (on-denial memory write) — Scenario 04 inherits but doesn't exercise; if a subagent's destructive tool is denied, the same write path applies.
- **G-014** (memory extraction) — Must scale to N parallel bundles; aggregation pass for G-022.
- **G-017** (concurrent sessions) — Now stress-tested at N=4 children per project simultaneously. KB upload concurrency is the hot path.

**Newly urgent.** G-020 and G-021 are *the* blockers that prevent scenario 04 from being a load-bearing self-improvement scenario. Without them, this scenario demonstrates *parallel orchestration*, not *learning orchestration*.

## 5. Open-question resolutions

| # | Question | Resolution |
|---|---|---|
| 1 | `reinforce_signal` aggregation rule | → G-020 ADR. AND-of-critical-path + user-veto. |
| 2 | Counterfactual DPO replay | → G-021 ADR. Projection algebra from observed failures. |
| 3 | Skill draft emission across subagents | Counter-only in v0.1 (G-023). |
| 4 | Kanban board persistence on crash | Local file mirror (G-025). |
| 5 | Aggregate memory facts | v0.1: `#stat:` prefix in `Fact`; v0.2: new `Statistic` variant (G-022). |
| 6 | Resource quota across siblings | Per-coordinator ceiling sufficient for v0.1 (no change). |
| 7 | Sibling-message ordering | Best-effort + per-pair FIFO; pin in plan/05a §"Inter-agent messaging". |

## 6. Critical insight

**Scenario 04 is the inflection point.** This is where Lamark stops being a "better local agent" and starts being a system that *learns to orchestrate*. The single-agent traces from scenarios 01–03 are valuable SFT data, but they teach the model *how to be a tool-using assistant*. The traces from scenario 04 teach it *how to be a coordinator* — when to spawn N=3 vs N=1, when to re-plan vs retry, which children to cancel under budget pressure. These are *qualitatively different* decisions and they need their own training signal.

The plan suite has been designed assuming this signal will fall out naturally from `interaction_edges`. It won't. Edges record *what happened*; DPO needs *what could have happened*. Without G-020 (tree-level outcome aggregation) and G-021 (counterfactual orchestration trajectories), the trainer can only learn "this run worked" — it cannot learn "this run worked *better than the alternative*."

The remedy is not new infrastructure; it's a tight ADR pair that locks the aggregation predicate and the counterfactual projection algebra. Once they exist, scenario 04 becomes the first test of the full closed loop at multi-agent scale: traces → DPO pairs → adapter → improved coordinator → better traces. Until then, scenario 04 is a demo, not a learning system.

## 7. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-0020 | Multi-agent outcome aggregation (`reinforce_signal` across a tree) | **Blocker** (closes G-020) |
| ADR-0021 | Counterfactual orchestration DPO — projection algebra & schema | **Blocker** (closes G-021; companion to ADR-0009) |
| ADR-0022 | Aggregate-statistic memory kind (`#stat:` prefix in v0.1, new variant in v0.2) | Deferred (G-022) |
| ADR-0023 | Skill convergence-detection — counter-only in v0.1 | Deferred (G-023) |
| ADR-0024 | Cascading-cancel deadline (`coordinator.cancel_deadline_seconds`) | Deferred (G-024) |
| ADR-0025 | Kanban board durability — local file mirror + KB | Deferred (G-025) |
| ADR-0026 | Per-subagent tool-proxy mode defaults by role | Deferred (G-026) |
