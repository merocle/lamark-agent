# 05b — Tasks, Kanban, project boards

> Three task surfaces, one ladder. From a single agent's todo list, through
> multi-agent Kanban (parent orchestrator + child subagents), up to
> KB-persisted project boards that span sessions, agents, and days.

**Crate:** `crates/lamark-tasks/` (new).
**Depends on:** `lamark-core`, `lamark-hooks`, `lamark-tools`, `lamark-coordinator` (05a), `lamark-kb-client`.
**Cross-links:** plan/05a (the agent-to-agent half of Kanban); plan/02 (slash commands & CLI); plan/07a (KB persistence).

## The three layers

```
       ┌─────────────────────────────────────────────────────────────┐
       │ Layer 3: Project boards (KB-persistent)                     │
       │   spans sessions, agents, days; user-managed                │
       │   "ship the auth rewrite by Friday"                         │
       └────────────────────────────┬────────────────────────────────┘
                                    │ contains
                ┌───────────────────▼───────────────────┐
                │ Layer 2: Kanban (coordinator session) │
                │   spans subagents inside ONE rollout  │
                │   parent posts cards, children claim  │
                └────────────────────┬──────────────────┘
                                     │ contains
                          ┌──────────▼──────────┐
                          │ Layer 1: Tasks       │
                          │  ONE agent's todo    │
                          │  list within a turn  │
                          └──────────────────────┘
```

Each layer can promote up: a single-session task that the agent decides to fan out becomes a Kanban card; a Kanban board the user wants to persist becomes a project board.

## Layer 1 — Tasks (single agent's todo list)

The Claude-Code pattern. A live agent maintains a list of todo items to organize a complex multi-step request. Visible to the user; survives compaction; does NOT cross sessions.

### Data model

```rust
pub struct Task {
    pub id: TaskId,                      // session-local id ("1", "2", ...)
    pub session_id: SessionId,
    pub subject: String,                 // imperative form, < 80 chars
    pub description: String,             // 1-2 sentences
    pub active_form: Option<String>,     // present-continuous label for spinners
    pub status: TaskStatus,              // pending | in_progress | completed | deleted
    pub blocks: Vec<TaskId>,
    pub blocked_by: Vec<TaskId>,
    pub owner: Option<String>,           // usually empty; set when delegated to a subagent
    pub created_at: SystemTime,
    pub updated_at: SystemTime,
    pub metadata: HashMap<String, Value>,
}

pub enum TaskStatus { Pending, InProgress, Completed, Deleted }
```

### Tools registered (Claude-Code-shaped)

These are the same tools we listed under "Task" in plan/05's catalog. Their implementations live in this crate:

| Tool | Effect |
|---|---|
| `TaskCreate` | Append a new Task (status: pending). |
| `TaskUpdate` | Mutate status / subject / description / dependencies. |
| `TaskGet` | Read one Task by id. |
| `TaskList` | Read all Tasks for the session (default) or KB-promoted (with `--scope=board:<id>`). |
| `TaskStop` | Mark deleted (does not actually delete; preserved in trace). |
| `TaskOutput` | Read recorded output / artifacts for a completed Task. |

### Lifecycle within the session

- The TUI shows the task list as a side panel (when `tasks.show_panel = true`, default in interactive mode).
- The gateway adapter for Slack/TG/Discord renders the task list as a follow-up message when it changes (rate-limited).
- On compaction, the task list is part of Tier-3 (memory snapshot). It stays in the prompt verbatim — small, useful.
- On session end, the task list serializes into the trace bundle as a `TraceEvent::TasksFinalState`.

### Per-task hooks (optional)

```
TaskCreated, TaskUpdated, TaskCompleted, TaskStopped
```

A user-defined hook can mirror task changes to an external system (linear, GitHub, Jira). Default: no external mirror; KB mirrors automatically when `tasks.kb_mirror = true`.

### Slash commands

```
/tasks                       # show the panel inline
/task new "<subject>"
/task done <id>
/task block <id> reason
/task promote <id>           # → Kanban card (Layer 2) or board card (Layer 3)
/tasks clear                 # reset for this session
```

### When the agent uses tasks

The system prompt's `Tier-1.GuidanceTasks` section instructs the agent (per Claude Code style):

> Use `TaskCreate`/`TaskUpdate` for any request that requires 3 or more discrete steps. Mark `in_progress` BEFORE starting each task; `completed` AS SOON AS each task is done; do not batch. After completing a task, check the list to find the next.

This isn't enforced; it's prompted behavior. The trace records compliance so the training pipeline can preference-pair sessions that follow vs ignore the convention.

### When the agent does NOT use tasks

- Single-step requests.
- Pure-conversational replies.
- Inside Curator runs (Curator's toolset excludes Task*).

## Layer 2 — Kanban (coordinator + subagents)

The contents of plan/05a, **except** the data model now derives from the same `Task` primitive as Layer 1. A Kanban card is a Task with extra fields and richer state.

### Promotion: Task → KanbanCard

```rust
pub struct KanbanCard {
    pub task: Task,                       // embedded; shares id-space scoped by board
    pub assignee_session: Option<SessionId>,   // which subagent has it
    pub last_heartbeat: Option<SystemTime>,
    pub result: Option<CardResult>,
    pub priority: i32,
    pub deadline: Option<SystemTime>,
    pub fail_count: u8,
    pub max_retries: u8,
}
```

When the user (or the agent itself) runs `/task promote <id>` from a coordinator session, the Task is upgraded:
1. The session's Kanban board (created on first `/team mode on`) gets a new card.
2. The Task in Layer 1 stays — it's still the orchestrator's "I'm tracking this" item. Its status mirrors the card status.
3. When the card is claimed by a subagent, the orchestrator's Task gets `owner = "<subagent_session_id>"` and `status = in_progress`.

This dual representation (orchestrator's Task + subagent-facing Card) is what lets the user see the same item from two angles: "what is the orchestrator working on" vs "which child has each work item."

### Reverse promotion (subagent posts a sub-task back)

A subagent can call `TaskCreate` within its own scope. If `coordinator.escalate_subtasks = true` and the subagent runs out of budget or hits a block, that subtask is re-posted to the parent Kanban as a new card with the original card as `blocked_by`.

### Kanban tools (already in 05a)

`kanban_post`, `kanban_claim`, `kanban_heartbeat`, `kanban_block`, `kanban_unblock`, `kanban_complete`, `kanban_fail`, `kanban_view`, `kanban_assign`, `kanban_cancel`, `kanban_join`. See plan/05a §"Kanban tools" for semantics.

### Layer 2 lives in: the coordinator session

In-memory + mirrored to KB (`coordinator.kanban.persist_to_kb = true`). When the coordinator session ends, the board state freezes; cards stay queryable in KB.

## Layer 3 — Project boards (KB-persistent)

Long-running. Spans sessions, multiple agents, days/weeks. Owned by the user (or by a designated agent service account). This is the closest analogue to Linear / Jira / GitHub Projects for Lamark.

### Data model

```rust
pub struct ProjectBoard {
    pub id: BoardId,
    pub project_id: String,               // KB namespace
    pub name: String,
    pub description: String,
    pub owner: BoardOwner,                // user_id | agent_id | shared
    pub columns: Vec<Column>,             // default: Backlog | In Progress | Blocked | Done
    pub created_at: SystemTime,
    pub archived: bool,
    pub access: BoardAccess,              // private | team | org-wide
}

pub struct BoardCard {
    pub id: BoardCardId,                  // ULID, stable across all sessions
    pub board_id: BoardId,
    pub task: Task,                       // same embedded primitive
    pub column: String,
    pub assigned_to: Option<Assignee>,    // user_id | agent_id | unassigned
    pub session_history: Vec<SessionId>,  // every session that touched this card
    pub child_kanban_cards: Vec<KanbanCardId>,
    pub deadline: Option<SystemTime>,
    pub estimate_minutes: Option<u32>,
    pub effort_minutes: Option<u32>,      // accumulated from session traces
    pub labels: Vec<String>,
    pub external_refs: Vec<ExternalRef>,  // {kind: "github_issue"|"jira_ticket", url, id}
}
```

### Storage

Entirely in knowledge-base (`/projects/{p}/boards`, `/projects/{p}/boards/{b}/cards`). Lamark caches the working subset in memory; persistent state-of-truth is KB.

### Tools (registered when `tasks.boards.enable = true`)

| Tool | Effect |
|---|---|
| `BoardList`        | List boards in the current project. |
| `BoardCardList`    | List cards on a board (filter by column, label, assignee, deadline). |
| `BoardCardGet`     | Read one card with full history (sessions, children, refs). |
| `BoardCardCreate`  | Create. Requires `boards.write` policy grant. |
| `BoardCardUpdate`  | Move column, assignee, dates, labels, refs. |
| `BoardCardComment` | Append a comment thread (markdown). |
| `BoardCardClose`   | Done / Won't fix / Duplicate; preserves history. |

Default policy: `prompt` for write tools (any agent must ask the user before mutating a long-running board). `Allow` for read tools.

### Promotion: Task or KanbanCard → BoardCard

- `/task promote <id> --board <name|id>` — explicit.
- Auto-promote (opt-in): if a Task in a `/goal` Ralph loop survives N iterations without completing, the coordinator can offer to promote it to a board card so the work continues across sessions.

### Session → board linkage

Every session bound to a board carries `metadata.board_id` and contributes:
- `effort_minutes` (computed from session duration on tasks linked to board cards).
- A `BoardCard.session_history` entry.
- Reinforce signals if the session resolved board work (plan/07a §"Reinforce signal").

### External refs

`ExternalRef` lets a BoardCard cite a GitHub issue, YouTrack ticket, Slack thread, Linear card. The training pipeline (plan/10) uses these to link agent work to ground-truth outcomes (issue closed? PR merged? ticket resolved?).

### CLI

```
lamark board list
lamark board show <board>
lamark board cards --board <board> [--column ...] [--assignee ...]
lamark board card new --board <board> "<subject>" [--deadline ...]
lamark board card update <id> --column "In Progress" --assignee me
lamark board card close <id> --reason "done|duplicate|wontfix"
lamark board promote-from-task <task_id> --board <board>
```

### Slash commands

```
/board list
/board cards
/board pin <id>                  # focus on this board for the session
/board new "<name>"
/board card new "<subject>"
/board mention <card_id>          # inject card context into the next prompt
```

### TUI panel (when interactive)

The right-hand pane in interactive mode toggles between:
- **Tasks** (Layer 1, session-local) — default.
- **Kanban** (Layer 2, coordinator session only).
- **Board** (Layer 3, when `/board pin <id>` is active).

`Ctrl-Tab` cycles.

## Promotion ladder summary

| Source | Destination | Trigger | Effect |
|---|---|---|---|
| Task → KanbanCard | `/task promote <id>` while in coordinator session | A subagent will work on it |
| KanbanCard → Task | `kanban_cancel` on a card the orchestrator still needs | Falls back to single-agent work |
| Task → BoardCard | `/task promote <id> --board <b>` | Survives session end |
| KanbanCard → BoardCard | `kanban_complete` with `--persist-as-board-card` | Long-running follow-up tracked |
| BoardCard → Task | New session with `/board card pickup <id>` | Bringing long-running work into a focused session |

The ladder is **explicit** — no auto-promotion across layers unless the user opts in via config (`tasks.auto_promote_*`).

## Configuration

```yaml
tasks:
  enable: true
  show_panel: true                       # TUI default
  kb_mirror: true                        # mirror Task changes to KB even before promotion
  guidance_in_prompt: true               # include the Tier-1 GuidanceTasks block

  kanban:                                # (defaults inherited from coordinator.kanban; see 05a)
    persist_to_kb: true
    audit_log_dir: "~/.lamark/kanban-logs"

  boards:
    enable: true
    default_board: ~                     # optional auto-pin
    write_policy: prompt                 # allow | prompt | forbid
    columns_default: ["Backlog", "In Progress", "Blocked", "Done"]
    auto_promote_ralph_stuck_after: 5    # iterations; 0 disables
```

## Policy hooks

```toml
# ~/.lamark/policy.toml
[tasks.boards]
write = "prompt"                          # all BoardCard* mutating tools
external_ref_create = "prompt"
external_ref_delete = "forbid"            # never let an agent delete ground-truth refs

[tasks.kanban]
zombie_kill = "allow"                     # the coordinator may force-cancel a stuck subagent's card
sibling_message = "prompt"
```

## Knowledge-base endpoints (informs `lamark-kb-client` and KB's RFC)

| Object | Endpoint |
|---|---|
| Project board | `GET /projects/{p}/boards`, `POST /projects/{p}/boards`, `PATCH /projects/{p}/boards/{b}`, `DELETE /projects/{p}/boards/{b}` |
| Board card | `GET /projects/{p}/boards/{b}/cards`, `POST`, `PATCH`, `DELETE` |
| Card comment | `POST /projects/{p}/boards/{b}/cards/{c}/comments` |
| Card history | `GET /projects/{p}/boards/{b}/cards/{c}/history` |
| Session → card link | `POST /agents/{a}/sessions/{s}/links` with `{board_card_id, effort_minutes}` |
| Kanban mirror | `POST /agents/{a}/sessions/{s}/kanban/{card_id}/events` (already in 05a) |
| Task mirror (Layer 1) | `POST /agents/{a}/sessions/{s}/tasks/{task_id}/events` |

All multi-tenant scoped by `project_id` (KB's namespacing model).

## Training-pipeline implications

Tasks & boards are first-class outcome signals:

- A board card closed `done` after N sessions where the agent participated → `Reinforce::PositiveOutcome` on the strategies + skills + memories that helped.
- A board card closed `wontfix` after agent work → neutral; no penalty.
- A Layer-1 Task left `in_progress` at session end with status reverted on next session → mild negative signal.
- An `external_ref` (GitHub issue) that closes after agent work → strong positive signal (ground truth).

The trainer (plan/10) treats these as one of the highest-quality preference-pair sources for DPO.

## Hooks fired

```
TaskCreated, TaskUpdated, TaskCompleted, TaskStopped
KanbanCardPosted, KanbanCardClaimed, KanbanCardCompleted   (also in 05a)
BoardCreated, BoardArchived, BoardCardCreated, BoardCardUpdated,
BoardCardClosed, BoardCardCommented, BoardCardLinkedToSession
```

The trace recorder writes every one; the KB client mirrors when `tasks.kb_mirror = true`.

## Tests

- **`tests/tasks/lifecycle.rs`** — create / update / complete; status transitions enforced.
- **`tests/tasks/persistence_kb.rs`** — `kb_mirror=true` → every state change lands in KB.
- **`tests/tasks/compaction.rs`** — task list survives a forced compaction.
- **`tests/tasks/promote_to_kanban.rs`** — Task → Kanban card; the original Task gets `owner=<child_session_id>`.
- **`tests/tasks/promote_to_board.rs`** — Task → BoardCard; cross-session pickup works.
- **`tests/tasks/policy_block_board_write.rs`** — `boards.write=forbid` rejects `BoardCardUpdate`.
- **`tests/tasks/external_ref_unmodifiable.rs`** — `external_ref_delete=forbid` cannot be overridden by a prompt.
- **`tests/tasks/training_signal.rs`** — closing a board card emits a Reinforce::PositiveOutcome to KB.

## Cutover gate (extends P5)

- ✅ Layer 1 tasks visible in the TUI panel; survive compaction.
- ✅ Layer 2 Kanban demoed by a `/goal` loop spawning 3 subagents, all visible on a `/kanban` view.
- ✅ Layer 3 board can be created via `lamark board new`, a card promoted from a Task, and re-picked-up in a fresh `lamark chat` session.
- ✅ KB roundtrip: every promotion / closure / comment lands and round-trips.
- ✅ Reinforce signal from a `done` board card lands in KB and is retrievable by the trainer.
- ✅ Policy `forbid` on `external_ref_delete` is not bypassable.
