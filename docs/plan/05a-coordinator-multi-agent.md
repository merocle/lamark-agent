# 05a — Coordinator & multi-agent framework

> Hermes-style orchestration: a parent agent that delegates work to N child
> subagents over a Kanban board, with zombie-detection, /goal Ralph loops,
> and isolated sessions. Codex multi-agent v2 + Hermes v0.13.0 mashed up.

**Crate:** `crates/lamark-coordinator/` (new; this plan slots between 05 and 06).
**Depends on:** `lamark-core` (Session, Event), `lamark-tools` (registers Kanban tools), `lamark-hooks`, `lamark-trace`, `lamark-policy`, `lamark-providers`.
**References:**
- Hermes-Agent v0.13.0 "Tenacity" — multi-agent Kanban + heartbeat + zombie detection (compass artifact §"Multi-agent orchestration"; hermes `agent/*` + `tools/kanban_*`).
- claude-code mirror — `coordinator/` directory (multi-agent coordinator mode + team mode).
- codex-rs multi-agent v2 — `spawn_agent`, `followup_task`, `send_message`, `close_agent`; `CollabAgentSpawnBegin/End`, `CollabAgentInteractionBegin/End`, `CollabWaitingBegin/End` events (`codex-rs/protocol/src/protocol.rs:1137`).

**Key papers (see [`plan/00f-literature-survey.md`](./00f-literature-survey.md) §4 for full catalog):**
- **AutoGen** (arXiv 2308.08155) — GroupChatManager ≈ coordinator; reply-function dispatch as alternative to pull-based Kanban.
- **Magentic-One** (arXiv 2411.04468) — Orchestrator ledger (goals + subgoal status + error notes) as explicit coordinator state; error-driven re-planning as first-class FSM transition.
- **AgentOrchestra / TEA protocol** (arXiv 2506.12508) — agents exposed as typed tools to coordinators; unifies subagent invocation and tool invocation under one interface.
- **HALO** (arXiv 2505.13516) — dynamic subagent instantiation at runtime rather than a fixed roster; coordinator decides agent type per subtask.
- **Trust Paradox** (arXiv 2510.18563) + **SoK Trust-Authorization** (arXiv 2512.06914) — Minimum Necessary Information (MNI) baseline; dynamic trust evaluation rather than static RBAC.

## What this layer is for

A single Lamark `Session` (plan/05) runs **one** conversation with **one** model. But many user tasks fan out:

> "scrape these 50 sites, fact-check each, write a report, then post a draft to Slack."

That's 50 + 1 + 1 = 52 logical agents. We don't want all 52 in one conversation (context cost, fragility, no parallelism, no error isolation). We want a **parent orchestrator** + N **subagents** + a **shared Kanban board** for task state.

This crate provides that.

## Roles

| Role | What it does |
|---|---|
| **Coordinator** (a.k.a. orchestrator) | The parent Session. Posts task cards, monitors progress, aggregates results. Decides when to spawn / abort / re-task subagents. |
| **Subagent** | A child Session with its own conversation, toolset, environment, and budget. Pulls one or more cards from the board, works on them, posts results back. |
| **Kanban board** | Shared state machine (cards with `pending|in_progress|blocked|done|failed`). One per Coordinator. |
| **Conductor** (optional) | A meta-coordinator that runs over multiple Coordinators (e.g., one per project). Same protocol, one level up. v0.2. |

## When the coordinator is invoked

Three triggers:

1. **Tool call**: the running agent calls the `spawn_agent` tool → coordinator routes the new subagent.
2. **`/goal <objective>` slash command**: user kicks off a Ralph-loop (a self-looping refine-the-goal-until-done flow) — see §"Ralph /goal loop" below.
3. **Submission `Op::SpawnSubagent`** from the gateway or MCP server — programmatic.

All three converge on the same `Coordinator::spawn(spec)` API.

## Subagent isolation

Per hermes v0.13.0 invariants and codex multi-agent v2:

| Concern | Isolation guarantee |
|---|---|
| Conversation history | **Disjoint.** Subagent doesn't see parent's history. Parent doesn't see subagent's intermediate turns; only the **final summary** comes back. |
| System prompt | Inherited from parent role-spec, can be overridden per subagent. |
| Tools | Configurable. Default: same as parent. Can be narrowed (e.g., "no `Bash`, just `WebFetch` + `MemoryWrite`"). |
| Sandbox | Independent. Coordinator builds an `AgentSpec` and calls `sandbox.spawn_agent(spec)` (plan/05c). Default by config: `LocalSandbox(in-process)` for trusted internal roles; `DockerSandbox` for prompt-derived work. The coordinator never constructs `Session` directly. |
| Memory | Reads from same KB project; writes are tagged with `parent_session_id`. |
| Trace | Subagent gets its own `rollout_id`; parent's trace has `SubagentSpawned { child_rollout_id }` event with `interaction_edges` to the child. |
| Cancel | Cancelling the parent cancels all subagents (cascading cancel). Cancelling a subagent does NOT cancel the parent. |
| **Context delegation (MNI)** | **Each subagent receives only the context required for its subtask.** The coordinator must strip or gate parent session context before passing it to `AgentSpec.system_prompt_override`. This is the Minimum Necessary Information (MNI) principle from arXiv 2510.18563 — increasing inter-agent context improves task success but proportionally raises authorization drift. |

This matches the compass artifact: *"Only the final summary returns to the parent (zero context-cost intermediate steps)."*

### Agent-as-tool unification (TEA protocol)

Per AgentOrchestra (arXiv 2506.12508), subagent invocation and tool invocation should be **identical at the policy boundary**. Both go through the same `PermissionRequest` / `Decision` path in `lamark-policy`. The coordinator never calls `sandbox.spawn_agent` without emitting a `PermissionRequest { summary: "spawn subagent: <role>", decision_hint: Prompt }` first, subject to the same `Allow|Prompt|Forbidden` rules as any tool.

Concretely, `spawn_agent` is a registered `lamark-tools` tool (not a special coordinator bypass), so:
- Policy rules can match on `tool_name = "spawn_agent"` and `role = "<subagent_role>"`.
- The trace records it as a `ToolCallStarted { name: "spawn_agent" }` event.
- The sandbox choice is part of the tool's argument schema — not a hidden side effect.

## Kanban board

```rust
pub struct Card {
    pub id: CardId,
    pub title: String,
    pub description: String,
    pub status: CardStatus,           // pending|in_progress|blocked|done|failed|cancelled
    pub assignee: Option<SessionId>,  // which subagent has it
    pub blocked_by: Vec<CardId>,
    pub blocks: Vec<CardId>,
    pub created_by: SessionId,
    pub created_at: SystemTime,
    pub claimed_at: Option<SystemTime>,
    pub last_heartbeat: Option<SystemTime>,
    pub deadline: Option<SystemTime>,
    pub result: Option<CardResult>,   // summary text + payload refs
    pub metadata: HashMap<String, Value>,
}

pub struct KanbanBoard {
    cards: DashMap<CardId, Card>,
    coordinator: SessionId,
    audit_log: Mutex<Vec<KanbanEvent>>,
}
```

State machine (per card):

```
   pending ─── (assignee picks up) ──▶ in_progress
                                          │
                            ┌─────────────┼─────────────┐
                            │             │             │
                            ▼             ▼             ▼
                         blocked         done         failed
                            │             │             │
                  (block resolved)        │             │ (retry or escalate)
                            ▼             │             ▼
                       in_progress    (terminal)    pending|cancelled
```

## Kanban tools (exposed to agents)

These are real `lamark-tools` registered when `coordinator.enable=true`:

| Tool | Who calls it | What it does |
|---|---|---|
| `kanban_post`         | coordinator | Create cards. Can post a batch. |
| `kanban_claim`        | subagent    | Atomically move pending → in_progress for a card; fails if already claimed. |
| `kanban_heartbeat`    | subagent    | Refresh `last_heartbeat`. Must be called every ≤30s; otherwise the card becomes a zombie. |
| `kanban_block`        | subagent    | Move in_progress → blocked with reason + needed-input. |
| `kanban_unblock`      | coordinator | Move blocked → pending (with new context if needed). |
| `kanban_complete`     | subagent    | Move in_progress → done with result + summary. |
| `kanban_fail`         | subagent    | Move in_progress → failed with reason. |
| `kanban_view`         | any         | Read the board; can filter by status, assignee, etc. |
| `kanban_assign`       | coordinator | Re-assign a card (post-claim, before pickup). |
| `kanban_cancel`       | coordinator | Mark a card cancelled. Propagates to assigned subagent as `Submission::Interrupt`. |
| `kanban_join`         | subagent    | Subscribe to events for cards the agent is interested in (await blocks resolving). |

The tools are thin wrappers around `Arc<KanbanBoard>` methods. The board itself is in-memory (per coordinator session) and mirrored into the trace bundle on each mutation.

## Coordinator tools (parent-only)

| Tool | What it does |
|---|---|
| `spawn_agent`     | Build an `AgentSpec` (plan/05c) and call `sandbox.spawn_agent(spec)`. Fields: `{ role, system_prompt_override?, tool_allowlist?, tool_proxy?, sandbox_override?, egress?, budget }`. Returns the child's `session_id`. |
| `followup_task`   | Send a follow-up task to an *existing* subagent (rather than spawn a new one). |
| `send_message`    | Inter-agent message (sibling-to-sibling, mediated by coordinator). |
| `close_agent`     | Terminate a subagent (graceful). |
| `subagent_status` | Query running subagents: which cards, which conversation depth, last heartbeat. |

These match codex multi-agent v2 (`spawn_agent`, `followup_task`, `send_message`, `close_agent`) verbatim by name and roughly by semantics.

## Subagent lifecycle (events)

```
SubagentSpawned       (parent emits; trace + coordinator log)
SubagentStarted       (child emits its own SessionStarted)
SubagentClaimed       (child kanban_claim)
SubagentHeartbeat     (child kanban_heartbeat; every ≤30s)
SubagentBlocked       (child kanban_block)
SubagentUnblocked     (parent kanban_unblock)
SubagentResult        (child kanban_complete or kanban_fail)
SubagentClosed        (parent close_agent OR budget exhausted)
```

These map 1:1 to trace events. The reducer (plan/06) builds `interaction_edges` between parent and child sessions from these.

## Heartbeat + zombie detection

A subagent that holds a card but stops calling `kanban_heartbeat` for more than `heartbeat_grace` (default 90s) is considered a **zombie**. Coordinator behavior:

1. Mark the card `in_progress` → `blocked` with reason `"zombie: <session_id>"`.
2. Send `Submission::Interrupt` to the subagent.
3. After `zombie_grace` (default 30s more), force-close the subagent via `AgentHandle::kill()` (plan/05c). The Sandbox backend (e.g., `DockerSandbox`) runs its container/process cleanup.
4. Re-post the card to pending OR escalate, per coordinator policy.

Zombie detection is a `tokio::time::interval` task on the coordinator side, ticking every 10s.

## Ralph /goal loop

Hermes v0.13.0 added a **Ralph loop**: a `/goal <objective>` slash command kicks off a self-looping refine-the-goal-until-done flow with a locking primitive that prevents concurrent /goal sessions on the same workspace.

Implementation:

```rust
// crates/lamark-coordinator/src/ralph.rs

pub async fn run_goal_loop(
    coord: &Coordinator,
    objective: String,
    workspace: PathBuf,
    cancel: CancellationToken,
) -> Result<RalphOutcome> {
    let lock = WorkspaceLock::acquire(&workspace).await?;  // fail if another /goal active
    let mut iter = 0;
    let max_iter = coord.config.ralph.max_iterations.unwrap_or(20);
    let mut last_outcome = RalphOutcome::Incomplete;

    loop {
        if iter >= max_iter { return Ok(RalphOutcome::IterationLimit); }
        if cancel.is_cancelled()  { return Ok(RalphOutcome::Cancelled); }

        // 1. The coordinator agent receives the current goal + last attempt summary.
        let prompt = build_ralph_prompt(&objective, &last_outcome, iter);
        let attempt = coord.run_one_turn_pair(prompt, cancel.child()).await?;

        // 2. The coordinator decides: spawn subagents, refine objective, or declare done.
        match attempt.decision {
            RalphDecision::Done(result) => return Ok(RalphOutcome::Done(result)),
            RalphDecision::SpawnAndRetry { specs } => {
                let results = coord.run_subagents(specs).await?;
                last_outcome = RalphOutcome::Partial(results);
            }
            RalphDecision::Refine { new_objective } => {
                objective = new_objective;
                last_outcome = RalphOutcome::Refined;
            }
            RalphDecision::Block { reason } => {
                last_outcome = RalphOutcome::Blocked(reason);
                break;
            }
        }
        iter += 1;
    }
    drop(lock);
    Ok(last_outcome)
}
```

`WorkspaceLock` is a file-system advisory lock at `<workspace>/.lamark/.goal.lock`. Stale-lock detection: if the lock file's PID is dead, take over with a warning.

## Configuration

```yaml
coordinator:
  enable: true
  max_spawn_depth: 3                     # parent → child → grandchild, no further
  max_concurrent_subagents: 8
  heartbeat_grace_seconds: 90
  zombie_grace_seconds: 30
  default_subagent_budget:
    seconds: 600
    tokens:  200000
    iterations: 40
  isolation:
    env: per_subagent_container          # per_subagent_container | shared_workspace | inherit_parent
    memory_writes: tagged                # tagged | sandboxed | disabled
  kanban:
    persist_to_kb: true                  # mirror board state into knowledge-base
    audit_log_dir: "~/.lamark/kanban-logs"

ralph:
  enable: true
  max_iterations: 20
  workspace_lock: filesystem             # filesystem | kb
```

## Inter-agent messaging

`send_message` is sibling-to-sibling-via-coordinator. Routing:

```rust
pub async fn send_message(
    &self,
    from: SessionId,
    to: SessionId,
    payload: MessagePayload,
) -> Result<()> {
    // policy check: both subagents must share a coordinator
    self.policy.evaluate_sibling_msg(from, to)?;
    // route via coordinator's broadcast
    self.coordinator_tx.send(CoordinatorMsg::Inter { from, to, payload })?;
}
```

The receiving subagent gets a `Submission::IncomingSiblingMessage` injected into its SQ. It can choose to process it on the next turn (or never).

## Trace integration

Coordinator emits a superset of events into the shared event bus. The reducer adds:

```json
"interaction_edges": [
  { "source_id": "<parent_session_id>", "target_id": "<child_session_id>", "kind": "spawn"  },
  { "source_id": "<child_session_id>",  "target_id": "<card_id>",          "kind": "claim"  },
  { "source_id": "<child_session_id>",  "target_id": "<parent_session_id>","kind": "result" },
  { "source_id": "<child_a>",           "target_id": "<child_b>",          "kind": "message"}
]
```

This is the *training signal* for multi-agent behaviors. DPO pairs can be built per-edge ("when coordinator chose to spawn N=3 children, the outcome rate was X; when it chose N=1, Y; preference X over Y if X delivered earlier").

## Knowledge-base mirror

If `coordinator.kanban.persist_to_kb=true`:

- Every Kanban mutation → `POST /agents/{coordinator_session_id}/kanban/{card_id}/events`.
- Each card has a state row in KB with full audit trail.
- Cross-session insight: KB can answer "show me all blocked cards across all coordinators in this project," "what's the success rate of `spawn_agent` with budget=600s vs 1200s," etc.

## CLI surface

```
lamark goal "<objective>"                    # /goal Ralph loop from CLI
lamark agents list                           # show running subagents
lamark agents kill <session_id>              # force-close a subagent
lamark agents traces <coordinator_id>        # bundle paths for parent + children
lamark kanban view [--board <coordinator>]   # human-readable Kanban dump
lamark kanban replay <coordinator_id>        # tail Kanban events for an old session
```

## Slash commands (interactive)

When the user is in a chat session, these become available:

```
/goal <objective>            # start Ralph loop
/spawn <role> -- <prompt>    # quick spawn-and-await
/subagents                   # list current children + status
/kanban                      # show board
/kanban claim <card_id>      # manually claim (mostly for debugging)
/team mode on|off            # switch session into coordinator role
```

## Hooks specific to coordination

Beyond `SubagentSpawned` etc., the hook bus emits:

| Hook | Fired by | Useful for |
|---|---|---|
| `KanbanCardPosted`     | `kanban_post` | Notify a UI, mirror to KB. |
| `KanbanCardClaimed`    | `kanban_claim` | Update progress dashboards. |
| `KanbanCardCompleted`  | `kanban_complete` | Append to result-aggregation buffer. |
| `KanbanZombieDetected` | heartbeat ticker | Alert; auto-respawn policy. |
| `RalphIterationStarted`| /goal loop  | Visualize the loop. |
| `RalphLoopComplete`    | /goal loop  | Sound a chime; KB writeback. |
| `SiblingMessageSent`   | `send_message` | Trace edge. |

## Policy hooks

The coordinator subsystem honors per-tool policy (plan/05) plus its own:

```toml
# ~/.lamark/policy.toml — coordinator section
[coordinator]
allow_spawn_agent = "prompt"     # allow | prompt | forbid
max_spawn_depth = 3
allowed_roles = ["researcher", "implementer", "reviewer"]
forbid_recursive_spawn_within = "2m"   # prevent runaway fork bombs
```

A subagent attempting `spawn_agent` past `max_spawn_depth` gets a `PolicyForbidden` error and the parent sees it as a tool failure.

## Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| Subagent infinite loop (no progress) | Heartbeat staleness → zombie kill. Per-card deadline → auto-fail. |
| Subagents thrash on shared file | Conflict groups (plan/05); coordinator policy can route writes to a single "writer" subagent. |
| Cost runaway | Per-subagent `budget_tokens` + `budget_seconds`. Coordinator-wide `max_concurrent_subagents`. Cron alert on total spend. |
| Parent dies, children orphaned | On coordinator session close, all children get cascading cancel. If the OS process dies, a cleanup task runs at next `lamark` startup to reap any orphans by `coordinator_session_id`. |
| Deadlock (cycle in `blocked_by`) | Cycle detection in `kanban_block` → reject. |
| Same card claimed twice | DashMap CAS; only one wins; loser gets `KanbanAlreadyClaimed`. |
| Subagent posts garbage result | Coordinator can `kanban_reopen` with a critique; subagent re-attempts. |

## Coordinator reinforce signal aggregation

The coordinator's `reinforce_signal` for a multi-agent rollout is computed over the critical path of the Kanban DAG:

- **`Success`** — if and only if every card on the critical path (cards whose completion was required for the goal, connected via `depends_on` links from the root goal card to the final output card) has `reinforce_signal = Success`.
- **`Fail`** — if any critical-path card has `reinforce_signal = Fail`.
- **`Mixed`** — if critical-path cards carry mixed signals (some `Success`, some `Fail`).
- **`Unknown`** — if any critical-path card has no signal yet at session end (this should not occur under normal termination; treat as `Fail` in aggregation logic).
- **User-veto override.** If the user explicitly rejected the coordinator's plan at any point — a `UserInput` event that was classified as a plan rejection — the aggregated signal is `Fail` regardless of individual card outcomes.

The aggregated signal is written to `manifest.json` as `reinforce_signal` for the coordinator's own trace bundle. The critical path is determined from the `depends_on` edges in the final Kanban snapshot: the sequence of `card_id`s forming the longest dependency chain from the root goal card to the final output card.

## Counterfactual DPO for orchestration decisions

The coordinator produces DPO training pairs for three orchestration decision types. All pairs are written to the coordinator's `reduced/dpo_pairs.jsonl`.

**Admissibility rule.** A rejected trajectory is only emitted when the coordinator had observational evidence at decision time that the original plan would fail (e.g., a heartbeat miss, an error response from a subagent, a blocked card exceeding its deadline). Speculative rejections — where the coordinator had no such evidence — are **not** emitted.

1. **Spawn decision.** When the coordinator spawns N subagents for a task and some fail but the coordinator recovers (re-spawns or re-routes to achieve the goal), emit:
   ```
   DpoPair {
     chosen:   recovery_plan_trajectory,
     rejected: projected_continuation_of_failed_plan,
   }
   ```
   The `rejected` trajectory is constructed deterministically from the last known subagent state at the point of failure.

2. **Cancel decision.** When the coordinator cancels a subagent mid-task and achieves the goal through an alternative path, emit a DPO pair where `rejected` is the projected outcome of allowing the cancelled subagent to continue running.

3. **Re-plan decision.** When the coordinator detects a blocked card and restructures the Kanban DAG, emit:
   ```
   DpoPair {
     chosen:   replanned_trajectory,
     rejected: original_plan_projected_as_if_continued,
   }
   ```
   The `rejected` trajectory is the original plan's projected execution as-if no re-plan had occurred, derived deterministically from the state at the moment re-planning was triggered.

## Tests

- **`tests/coord/spawn_basic.rs`** — coordinator spawns 3 subagents, each completes 1 card; result is aggregated.
- **`tests/coord/zombie.rs`** — subagent stops heartbeating → card moves to blocked → coordinator cancels and reposts.
- **`tests/coord/cascading_cancel.rs`** — Ctrl-C on parent kills all subagents within 2s.
- **`tests/coord/ralph_loop.rs`** — scripted provider drives a 3-iteration Ralph loop to `Done`.
- **`tests/coord/max_spawn_depth.rs`** — attempt to spawn at depth 4 → PolicyForbidden.
- **`tests/coord/sibling_message.rs`** — A sends → B receives within one turn.

## Cutover gate

- ✅ A scripted scenario "scrape 5 URLs in parallel, summarize" finishes in roughly (slowest single URL + coordinator overhead), not 5×.
- ✅ Killing a subagent does not crash the parent.
- ✅ Killing the parent reaps all children (verified by Docker `ps -a` showing no leftover containers).
- ✅ /goal loop runs end-to-end and the workspace lock survives a stale-PID scenario.
- ✅ Kanban state mirrors to knowledge-base round-trip in <1s p50, <5s p99.
