# 04 — Multi-agent Kanban + /goal Ralph loop

> **Phase:** P5 (`plan/00-overview.md` — earlier than skills if vertical-slice priority).
> **One-liner:** User types `/goal "scrape these 30 sites, fact-check each
> claim, post a summary to Slack"`. Coordinator decomposes into Kanban
> cards, spawns isolated subagents, heartbeats keep zombies in check; one
> subagent fails → coordinator re-plans; the final summary returns to the
> parent with full `interaction_edges` captured as **multi-agent DPO signal**.

---

## North-star contribution

- **Domain quality.** This is *the* test of whether Lamark beats a single-
  agent loop on long-horizon, fan-out work. Hermes Kanban + Codex multi-
  agent v2 are the design sources; the bet is that parallel isolated
  contexts deliver better outcomes than a single 200K-token conversation.
- **Agent-side self-improvement.**
  - The **coordinator's choices** are first-class training signal: when did
    it spawn N=3 vs N=1, when did it `kanban_unblock` vs `kanban_cancel`,
    when did it `followup_task` an existing subagent vs spawn fresh?
  - Each subagent's **own trace** becomes a separate Nemotron-Agentic-v1
    sample with a smaller, cleaner context window — *higher-quality SFT
    data per token* than monolithic traces.
  - **Skill drafts emerge naturally** from successful subagent patterns
    (e.g., "fact-check a claim" becomes a candidate skill if N independent
    subagents converged on the same tool sequence).
- **Model-side self-improvement.** The reducer emits `interaction_edges`
  in `state.json` (`plan/05a:262–269`). These are the **graph signal** for
  DPO on orchestration strategy:
  - *chosen* = the coordinator decision sequence that hit `Done`.
  - *rejected* = an alternative decision branch projected from observed
    failures (re-plan after subagent timeout, etc.).
  - Trainer can teach the model "prefer spawning N=3 when 30+ items, N=1
    when ≤5" by pairing concrete edges.

### Signals produced / consumed

- **Produces:**
  - One parent + N child trace bundles (each with own `rollout_id`).
  - `state.json::interaction_edges` for the whole tree.
  - KB Kanban audit trail (`POST /agents/{coordinator_session_id}/kanban/{card_id}/events`, `plan/05a:277`).
  - `reinforce_signal` per subagent + aggregated for the parent (G-004 / G-020 below).
  - Zombie / failure events — **strong negative samples** for training.
- **Consumes:**
  - `coordinator.max_spawn_depth`, `max_concurrent_subagents` (plan/05a:217).
  - Per-subagent `budget` from the spec (seconds / tokens / iterations).
  - Sandbox capabilities from plan/05c (the parent never invokes a sandbox
    directly — it `spawn_agent`s).

---

## Idea

A user runs `/goal "scrape these 30 product pages, fact-check every spec
claim, write a 5-bullet executive summary, post to #research"`. A single
turn-loop choking on 30 fetches + 30 cross-checks + 1 synthesis would be
~500 tool calls, ~150K tokens, and one network hiccup away from total
restart. Coordinator decomposes: 30 fetch cards + 30 check cards + 1
synthesize card + 1 post card; spawns 4 isolated subagents (one per
"shape" of work); heartbeats every ≤ 30 s; one subagent's container dies
mid-fetch → zombie detection → re-post the card; Ralph loop iterates once
to refine the synthesize prompt; final result posts to Slack.

## Actors

| Actor | Role |
|---|---|
| **User (Carol)** | Research lead. Types the `/goal` slash command. Watches the Kanban TUI panel; can interrupt. |
| **Coordinator session** | The parent `Session`. Owns the Kanban board, the Ralph loop, child lifecycle (`plan/05a §"Roles"`). |
| **Subagent A** (`scraper`) | Tool allowlist narrowed to `WebFetch + Read + kanban_*`. No `Bash`, no `Edit`. (`plan/05a §"Subagent isolation"`) |
| **Subagent B** (`fact_checker`) | `WebFetch + Grep + kanban_*` + an MCP fact-check tool. No file writes. |
| **Subagent C** (`summarizer`) | `Read + kanban_*` only. Larger context window allowance. |
| **Subagent D** (`poster`) | `kanban_*` + gateway-out (Slack adapter) only. Hardest permissions (network egress). |
| **Sandbox backend** | Per-subagent `DockerSandbox` by default (`plan/05c §"Sandbox trait"`), isolated network namespaces. |
| **Trace recorders** | One per session (parent + 4 children); each writes its own `rollout_id`. |
| **Kanban board** | In-process `Arc<KanbanBoard>` mirrored to KB via `POST /agents/{coordinator_session_id}/kanban/...` (`plan/05a:275–279`). |

## Trigger

```
$ lamark chat
> /goal scrape product pages at <list of 30 URLs>, fact-check spec claims, write 5-bullet summary, post to #research
```

The Ralph loop kicks off via `coord.run_goal_loop(...)` (`plan/05a:166`).

## Pipeline

### Step 0 — Goal lock + Ralph iteration 0

1. `WorkspaceLock::acquire(workspace)` (`plan/05a:174`) takes an advisory lock on `<workspace>/.lamark/.goal.lock`. If already held by a live PID, abort with a clear error. Stale-PID handling: take over with a warning.
2. Ralph iteration 0: coordinator agent runs a single turn pair with the goal prompt. Decision: `SpawnAndRetry { specs: [...] }` with 4 subagent specs.

### Step 1 — Kanban decomposition

3. Coordinator calls `kanban_post` (batched) to create 62 cards: 30 `fetch:url=...`, 30 `check:url=...,claim=...placeholder`, 1 `synthesize:depends_on=all_checks`, 1 `post:depends_on=synthesize`. (`plan/05a §"Kanban tools"`.)
4. The `check` and `synthesize` cards have `blocked_by` populated, so they sit in `pending` but can't be claimed until the upstream is `done`.
5. Each `kanban_post` mirrors to KB (`POST /agents/{coordinator_session_id}/kanban/{card_id}/events`) — board state is queryable cross-session for forensics (scenario 08 reuses this for multi-project insight).

### Step 2 — Spawn subagents

6. Coordinator calls `spawn_agent` 4× — one per role. `AgentSpec` (per `plan/05c`):
   - `role`, `system_prompt_override` (role-specific), `tool_allowlist` (narrowed), `sandbox_override` (Docker), `egress` (Slack only for poster; web for scraper + checker; none for summarizer), `budget` (defaults: 600s, 200K tokens, 40 iterations — `plan/05a:221–224`).
7. Each subagent's `Session` starts; `SubagentSpawned` event from parent → trace + coordinator log.
8. Children emit their own `SessionStarted` → independent trace bundles begin writing to `~/.lamark/traces/<child_rollout_id>/`.

### Step 3 — Concurrent execution + heartbeat

9. Each subagent runs a `kanban_view` to find pending cards matching its role tag.
10. `kanban_claim` atomically moves a card to `in_progress` with `assignee=self.session_id`. Conflicts (two subagents racing for the same card) are CAS-resolved; loser claims a different card.
11. Subagent works the card. **Every ≤ 30 s** it calls `kanban_heartbeat`, refreshing `last_heartbeat`. (`plan/05a:109`)
12. Coordinator's zombie-detection task ticks every 10 s, looking for cards where `now - last_heartbeat > heartbeat_grace=90s`. (`plan/05a:148–157`)
13. **Failure case (scripted in the test):** the scraper's Docker container OOMs at card 17 of 30. No heartbeat for 90 s → coordinator: (a) marks card → `blocked` with reason `"zombie: <session_id>"`, (b) sends `Submission::Interrupt` to the (dead) subagent, (c) after `zombie_grace=30s`, calls `AgentHandle::kill()` (no-op since already dead — `plan/05c`), (d) re-posts the card to `pending`, (e) **spawns a replacement subagent** with the same spec.
14. The replacement claims the failed card; eventually all 30 fetches `kanban_complete` with `result_ref` pointing to a payload in *its* trace bundle (parent does not see the body, only the summary text + a `PayloadRef`).

### Step 4 — Dependent cards unblock

15. As each `fetch:url=X` card hits `done`, the corresponding `check:url=X` card's `blocked_by` clears. Coordinator's `kanban_unblock` moves it to `pending`. Fact-checker subagents claim and process.
16. When all 30 checks `done`, the `synthesize` card unblocks. Subagent C claims, reads the 30 check results (via `Read` against `payloads/`-linked refs the coordinator surfaces), drafts 5 bullets, calls `kanban_complete`.

### Step 5 — Ralph iteration 1 — refinement

17. Coordinator inspects the synthesize result. Its decision logic (in the coordinator agent's reasoning) says: "summary too generic; refine prompt and rerun." Returns `RalphDecision::Refine { new_objective }`. (`plan/05a:194–197`)
18. Iteration 1 re-runs only the synthesize card with the refined prompt (it does NOT re-fetch / re-check). This is the *real* value of Ralph: cheap iteration on the synthesis step without redoing the work.

### Step 6 — Post + close

19. `synthesize` lands; coordinator unblocks `post`; subagent D calls the Slack adapter through the gateway (`plan/09 §"Gateway"`), receives confirmation, `kanban_complete`s.
20. Coordinator emits `RalphLoopComplete { outcome: Done }`. Lock released.
21. Children are `close_agent`-ed gracefully (`plan/05a:128`). Each child closes its trace bundle independently; recorder flush; reducer per child; KB upload per child.
22. Parent's trace closes. **`interaction_edges` are computed by the reducer** by joining `SubagentSpawned/SubagentResult/SiblingMessageSent` events with their child counterparts (`plan/05a:262–269`).

### Step 7 — Aggregation: reinforce_signal across the tree

23. Each child's `reinforce_signal` (G-004) is computed independently. Parent's `reinforce_signal` is an **aggregation**: success iff all critical-path children succeeded AND the user signaled satisfaction (no `/undo`, no `/stop` mid-run). Aggregation rule needs an ADR (G-020 below).
24. Skill candidate emission: if the four subagent traces share a recurring tool sequence (e.g., the fact-checker's `WebFetch → Grep → mcp__factcheck__query → kanban_complete`), `lamark-memory::extract` (G-014) writes a skill draft to `~/.lamark/skills/drafts/` for Curator (scenario 05) to review.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (workspace lock, Ralph iter 0) | `lamark-coordinator::ralph` | 05a §"Ralph /goal loop" |
| 1 (Kanban posting) | `lamark-coordinator::board`, `lamark-tools::kanban_*` | 05a §"Kanban board", §"Kanban tools" |
| 2 (spawn) | `lamark-coordinator`, `lamark-sandbox` (Docker default) | 05a §"Coordinator tools", 05c |
| 3 (heartbeat + zombie detection) | `lamark-coordinator::zombie_watch` | 05a §"Heartbeat + zombie detection" |
| 4 (dependent unblock) | `lamark-coordinator::board` | 05a §"Kanban tools" |
| 5 (Ralph iter 1 refine) | `lamark-coordinator::ralph` | 05a:194–197 |
| 6 (Slack post via gateway) | `lamark-gateway::slack` | 09 |
| 7 (reduce + interaction_edges + reinforce aggregation) | `lamark-trace::reducer` | 06 §"Reducer", 05a:262–271 + G-020 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Workspace already locked (live PID)** | `/goal` returns `WorkspaceLocked { holder_pid, since }`; no execution. |
| **Stale lock (dead PID)** | Take over with a TUI warning + audit-log entry. |
| **`max_spawn_depth` exceeded** | `spawn_agent` returns `Err(SpawnDepthExceeded)`; coordinator must work the card itself or `kanban_fail` it. |
| **`max_concurrent_subagents` exceeded** | New `spawn_agent` calls queue (FIFO) inside coordinator; logged as `Notification { level: Info }`. |
| **Subagent zombie (no heartbeat for 90s)** | Per step 13: block → interrupt → kill → re-post → re-spawn. Recorded as zombie event sequence. |
| **Subagent exceeds token budget** | Sandbox enforces; `AgentHandle::kill()` on budget hit; card → `failed` with reason `budget_exhausted`. Coordinator decides retry vs escalate. |
| **Sibling message routed to non-sibling** | `policy.evaluate_sibling_msg` denies (`plan/05a:248–250`); recorded as a permission denial; no crash. |
| **KB unavailable for Kanban mirror** | Board still in-memory; mutations queue in outbox (per G-017 / scenario 03). No subagent blocked. |
| **User `/stop` mid-loop** | `cancel.cancel()` propagates: each subagent receives `Submission::Interrupt`; in-flight tool calls finish or abort per tool semantics; all children `close_agent`-ed; trace bundles seal with `status=aborted`. |
| **Two subagents race for the same card** | CAS in `kanban_claim`; loser retries with a different `kanban_view` result. |
| **Inter-agent message arrives after recipient closed** | Bounced back to coordinator as `MessageUndeliverable`; recipient is gone, message is logged. |
| **Ralph iteration limit hit (`max_iterations=20`)** | `RalphOutcome::IterationLimit`; partial result returned with whatever cards are `done`. |
| **Slack post fails (poster subagent)** | `kanban_fail` on the `post` card; coordinator can retry or escalate; user notified. **Does not** roll back the synthesized summary — it's persisted in payloads. |

## Acceptance criteria

- [ ] `/goal "…"` triggers `run_goal_loop` with a workspace lock; concurrent `/goal` on the same workspace is rejected.
- [ ] Coordinator can post ≥ 50 cards in one `kanban_post` batch; all mirror to KB.
- [ ] Four spawned subagents run with **disjoint conversation histories** (verified by inspecting trace bundles — child trace contains no parent messages, only the `AgentSpec`).
- [ ] Each subagent has a tool allowlist consistent with its spec (e.g., poster cannot call `Bash`).
- [ ] A subagent that fails to heartbeat for 90s is detected within 100s and force-killed by 130s; its card is re-posted.
- [ ] The replacement subagent picks up the re-posted card and completes it.
- [ ] `state.json::interaction_edges` includes `spawn`, `claim`, `result`, and `message` kinds with non-empty source/target ids (`plan/05a:262–269`).
- [ ] Parent trace contains a `SubagentResult` (or equivalent) event for every child; child trace contains a `SessionEnded` event.
- [ ] Each child writes an independent reduced bundle; parent's reduced bundle references them by `child_rollout_id`.
- [ ] Parent's `manifest.reinforce_signal` is computed via the aggregation rule from G-020 and matches the test fixture's expected value.
- [ ] User `/stop` causes a full cascading cancel within 3 seconds.
- [ ] `max_spawn_depth=3` is enforced; `spawn_agent` from a grandchild returns `SpawnDepthExceeded`.

## Self-improvement assertions

1. **Per-subagent traces are SFT-quality.** Each child's `reduced/conversation.jsonl` is shorter and more topical than the equivalent single-session monolith would have been. *Measurement:* average token count per child < 1/N of the equivalent single-context session.
2. **`interaction_edges` enable orchestration-DPO.** From scenario 04's bundle, the trainer can construct a DPO pair `(spawn_n=4_chosen, spawn_n=1_rejected)` where the *rejected* trajectory is projected from a counterfactual constructed by replay — see G-021 below for the schema.
3. **Zombie detection produces high-value negative samples.** The trace event sequence `kanban_claim → (silence) → zombie → kanban_block → re-post → success` is a clear "what NOT to do" sample. Trainer can use it to teach the model to emit `kanban_heartbeat` reliably.
4. **Coordinator decision rate.** After scenario 04 runs N times, the prompt composer (next time the coordinator faces a similar goal) recalls memory facts of the form `"on 30+ item fan-out, N=4 with budget 600s succeeded in ≤ 10 minutes 8/10 times"`. (Memory-extraction step G-014 must support these aggregate facts; see G-022.)
5. **Skill emergence.** If the same fact-checker tool sequence converges across ≥ 3 independent subagents, a skill draft is written to `~/.lamark/skills/drafts/`. Curator (scenario 05) promotes it if usage continues across runs.
6. **Reinforce-aggregation transparency.** Parent manifest's `reinforce_signal` can be expanded into a child-by-child breakdown, viewable in `lamark trace inspect`.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Coordinator + Kanban roles | plan/05a §"Roles" | _audit_ |
| Card state machine + tools | plan/05a §"Kanban board", §"Kanban tools" | _audit_ |
| `spawn_agent` / `followup_task` / `close_agent` | plan/05a §"Coordinator tools" | _audit_ |
| Per-subagent isolation (conversation / tools / sandbox / memory tags) | plan/05a §"Subagent isolation" | _audit_ |
| Heartbeat + zombie detection algorithm | plan/05a §"Heartbeat + zombie detection" | _audit_ |
| Cascading cancel + budget enforcement | plan/05a §"Configuration", plan/05c §"Budget enforcement" | _audit_ |
| Ralph loop + workspace lock | plan/05a §"Ralph /goal loop" | _audit_ |
| `interaction_edges` in `state.json` | plan/05a §"Trace integration" | _audit_ |
| KB Kanban mirror (`POST /agents/.../kanban/.../events`) | plan/05a §"Knowledge-base mirror" | _audit_ |
| Subagent trace bundle independence (own `rollout_id`) | plan/06 + plan/05a:54 | _audit_ |
| **Aggregating `reinforce_signal` across a tree of bundles** (G-020) | (likely **gap**) | _audit_ |
| **Counterfactual DPO replay for spawn-N decisions** (G-021) | (likely **gap**) | _audit_ |
| **Memory facts as aggregate stats (vs single fact)** (G-022) | (likely **gap**) | _audit_ |
| Sibling-message routing + policy | plan/05a §"Inter-agent messaging" | _audit_ |
| Slack post via gateway (poster role) | plan/09 §"Gateway" | _audit_ |
| Skill draft emission from convergent subagent patterns | plan/08 §"Curator" — likely partial | _audit_ |
| Max-spawn-depth + max-concurrent-subagents enforcement | plan/05a §"Configuration" | _audit_ |
| TUI Kanban panel | plan/02 + 00d §14 | _audit_ |
| Per-subagent egress policy (network namespaces) | plan/05c §"Egress policy" | _audit_ |
| `WorkspaceLock` filesystem + stale-PID detection | plan/05a:174–210 | _audit_ |

## Open questions

1. **`reinforce_signal` aggregation rule across a tree.** AND of critical-path children? Weighted by token count? User satisfaction as veto? → ADR (likely G-020).
2. **Counterfactual replay for DPO.** To produce `(spawn_n=4_chosen, spawn_n=1_rejected)` we need a way to construct the rejected trajectory without running it. Same conceptual problem as G-008 / G-012 but at *coordinator* granularity. Likely needs its own schema.
3. **Skill draft emission across subagents.** The convergence-detection algorithm — Levenshtein over tool-call sequences? Embedding similarity? Counter-based ("seen 3+ times")? Pin in plan/08.
4. **Kanban board persistence model — in-process or KB?** Plan/05a:119 says "in-memory (per coordinator session) and mirrored." But on coordinator crash, does the in-memory board survive? Recoverable from KB mirror? An ADR on board durability.
5. **Aggregate memory facts.** A fact like *"on 30+ item fan-out, N=4 succeeded 8/10 times"* is a different shape than a single-session fact. Likely needs a `MemoryKind::Statistic` or similar.
6. **Resource-quota across siblings.** `max_concurrent_subagents=8` is global to the coordinator. What if two coordinators run concurrently in two `/goal` sessions across workspaces? Need a process-wide ceiling too? Probably not for v0.1.
7. **Sibling-message ordering guarantees.** FIFO from sender? Across-sender ordering? Likely best-effort + per-pair FIFO.
