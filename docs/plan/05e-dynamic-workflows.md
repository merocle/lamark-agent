# 05e — Dynamic Workflow Engine

> The model writes an orchestration plan on the fly; the engine executes it
> deterministically with a fleet of parallel subagents.
>
> Inspired by Claude Code's research-preview "dynamic workflows" feature.
> Complements the existing Kanban multi-agent protocol (plan/05a) but with
> a fundamentally different control-flow model: **plan-then-execute** vs
> **ad-hoc task board**.

**Crate:** `crates/lamark-workflow/`  
**Depends on:** `lamark-core`, `lamark-coordinator`, `lamark-sandbox`, `lamark-tools`  
**See also:** [`plan/05a`](./05a-coordinator-multi-agent.md) (Kanban), [`plan/10e`](./10e-workflow-training-dataset.md) (training data)

---

## 1. The core idea

```
User: "Audit all 47 Rust crates for security issues and generate a report"
          │
          │ model detects: "workflow" keyword or task complexity threshold
          ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 1 — Plan generation  (one LLM call, structured output) │
│                                                               │
│  Model writes WorkflowPlan:                                   │
│    Phase 1 "Scan" — 47 tasks (one per crate), parallel       │
│    Phase 2 "Analyze" — pipeline over Phase 1 findings        │
│    Phase 3 "Report" — sequential synthesis                    │
└─────────────────────┬───────────────────────────────────────┘
                      │ plan JSON
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 2 — Deterministic execution  (WorkflowEngine)          │
│                                                               │
│  Phase 1: spawn 47 subagents in parallel (batch of 16 max)   │
│  Phase 2: pipeline results through analysis agents           │
│  Phase 3: single synthesis agent reads all Phase 2 outputs   │
│                                                               │
│  No further LLM planning calls — execution is deterministic. │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
Trace bundle with sub-rollouts linked by workflow_id
Posted to KB → training data for workflow behavior
```

**Key distinctions from Kanban (plan/05a):**

| | Kanban | Dynamic Workflow |
|---|---|---|
| Plan created by | Agent during execution (reactive) | Model in one upfront call (proactive) |
| Control flow | Ad-hoc heartbeat + task pull | Deterministic phases with barriers |
| Agent lifecycle | Long-running, pull-based | Ephemeral, per-task |
| Parallelism | Emergent | Explicit (parallel / pipeline / sequential) |
| When to use | Open-ended exploration | Well-scoped decomposable tasks |

---

## 2. WorkflowPlan — the model output

The model generates a `WorkflowPlan` as structured JSON (via the `workflow_plan` tool):

```json
{
  "title": "Security audit — all Lamark crates",
  "description": "Scan each Rust crate for security issues in parallel, then synthesize.",
  "phases": [
    {
      "title": "Scan",
      "detail": "Run cargo-audit + clippy on each crate independently",
      "mode": "parallel",
      "tasks": [
        {
          "id": "scan-lamark-core",
          "label": "Audit lamark-core",
          "prompt": "Run cargo audit and cargo clippy -D warnings on lamark-core. Report all issues as JSON.",
          "schema": { "$ref": "CrateAuditResult" },
          "agent_type": "Explore"
        },
        ...47 tasks total...
      ]
    },
    {
      "title": "Analyze",
      "detail": "Classify and prioritize findings from Phase 1",
      "mode": "pipeline",
      "tasks": [
        {
          "id": "classify",
          "label": "Classify findings",
          "prompt": "Given the scan results from {phase.Scan}, classify each finding by severity.",
          "depends_on": ["phase:Scan"]
        }
      ]
    },
    {
      "title": "Report",
      "detail": "Write the final security report",
      "mode": "sequential",
      "tasks": [
        {
          "id": "report",
          "label": "Write report",
          "prompt": "Write a security report from the classified findings. Format as Markdown.",
          "depends_on": ["phase:Analyze"]
        }
      ]
    }
  ]
}
```

### Phase execution modes

| Mode | Semantics | Wall-clock |
|---|---|---|
| `parallel` | All tasks start simultaneously; no data sharing between them | Slowest single task |
| `pipeline` | Each task feeds the next; tasks A and B run concurrently once B can start | Sum of critical path |
| `sequential` | Tasks run one by one; later tasks see earlier results | Sum of all tasks |

### `depends_on` references

- `"task:<id>"` — wait for a specific task to complete and inject its result
- `"phase:<title>"` — wait for an entire phase and inject all results as a list
- Omitting `depends_on` → task starts as soon as its phase opens

---

## 3. WorkflowEngine — the executor

```rust
// lamark-workflow/src/engine.rs

/// Executes a `WorkflowPlan` deterministically by spawning subagents.
pub struct WorkflowEngine {
    pub coordinator: Arc<dyn SubagentCoordinator>,
    pub config: WorkflowEngineConfig,
}

pub struct WorkflowEngineConfig {
    /// Maximum concurrent agents across all phases.
    pub max_concurrency: usize,       // default: 16 (matches Claude Code cap)
    /// Abort the entire workflow if this many tasks fail.
    pub max_failures: usize,          // default: 5% of total tasks
    /// Per-task timeout.
    pub task_timeout: Duration,       // default: 5 min
    /// Write intermediate results to KB after each phase.
    pub checkpoint_phases: bool,      // default: true
}

impl WorkflowEngine {
    pub async fn execute(
        &self,
        plan: WorkflowPlan,
        workflow_id: WorkflowId,
    ) -> WorkflowResult {
        let mut phase_results: HashMap<String, Vec<TaskResult>> = HashMap::new();

        for phase in plan.phases {
            let results = match phase.mode {
                PhaseMode::Parallel  => self.run_parallel(&phase, &phase_results).await,
                PhaseMode::Pipeline  => self.run_pipeline(&phase, &phase_results).await,
                PhaseMode::Sequential=> self.run_sequential(&phase, &phase_results).await,
            };
            phase_results.insert(phase.title.clone(), results);
        }
        WorkflowResult { workflow_id, phase_results }
    }
}
```

### Concurrency model

Tasks within a `parallel` phase are submitted to a bounded work-stealing pool
(cap: `max_concurrency`, default 16). Each task is a complete agent session
isolated in its own sandbox. No shared state between parallel tasks — they
communicate only through their return values (structured output).

---

## 4. Workflow activation

The workflow engine is triggered in two ways:

### 4.1 Keyword trigger
When the user message contains the word "workflow" (case-insensitive), the agent
enters workflow planning mode instead of normal turn mode:

```rust
// In the turn loop, before the first LLM call:
if ctx.user_input.to_lowercase().contains("workflow") {
    return workflow_mode(ctx).await;
}
```

### 4.2 Complexity threshold
When the coordinator determines the task exceeds the single-agent complexity
threshold (estimated token budget > 4× `max_context` or > 5 distinct sub-goals
detected), it suggests workflow mode to the user.

### 4.3 The `workflow_plan` tool
The agent can explicitly call `workflow_plan` as a tool to generate and submit
a structured plan. This is the structured-output path used for training:

```json
{
  "name": "workflow_plan",
  "description": "Generate an orchestration plan for a complex multi-step task. Use when the task requires parallel investigation or synthesis across many items.",
  "parameters": {
    "type": "object",
    "properties": {
      "title": { "type": "string" },
      "description": { "type": "string" },
      "phases": { "type": "array", "items": { "$ref": "#/definitions/Phase" } }
    },
    "required": ["title", "phases"]
  }
}
```

---

## 5. Crate layout

```
agent/crates/lamark-workflow/
├── Cargo.toml
└── src/
    ├── lib.rs          pub re-exports
    ├── plan.rs         WorkflowPlan, Phase, Task, PhaseMode types
    ├── engine.rs       WorkflowEngine: execute(), run_parallel(), run_pipeline()
    ├── tools.rs        workflow_plan tool + structured output schema
    ├── trigger.rs      keyword + complexity detection
    └── trace.rs        WorkflowTrace: links sub-rollouts to a workflow_id
```

**Dependency graph:**

```
lamark-workflow
  ├── lamark-core        (WorkflowId, ids, tool types)
  ├── lamark-coordinator (SubagentCoordinator, Kanban primitives)
  ├── lamark-sandbox     (subagent spawning)
  └── lamark-tools       (workflow_plan tool registration)
```

---

## 6. Trace format extension

Each workflow execution produces a linked bundle of sub-rollouts:

```
~/.lamark/traces/<workflow_id>/
├── workflow_manifest.json
│   { workflow_id, plan_title, phases, started_at, ended_at,
│     total_tasks, succeeded, failed }
├── plan.json               ← the WorkflowPlan generated by the model
├── phase_Scan/
│   ├── task_scan-lamark-core/  ← one sub-rollout per task
│   │   ├── manifest.json
│   │   └── trace.jsonl
│   ├── task_scan-lamark-tools/
│   └── ...
├── phase_Analyze/
│   └── task_classify/
└── phase_Report/
    └── task_report/
```

Sub-rollout trace.jsonl entries include `workflow_id` and `phase_title` fields
for linking during reduction. The top-level KB upload includes the plan JSON
and the phase-level result aggregation.

---

## 7. Security and resource limits

- **Subagent sandbox isolation**: each task runs in its own sandbox instance
  (default: `docker`); no shared filesystem except explicitly passed inputs.
- **Budget cap**: total token budget across all subagents is bounded at
  `max_concurrency × task_budget`; refusing to spawn more if exceeded.
- **Egress**: subagents inherit the parent's egress policy (default: model-provider-only).
- **Permission**: the workflow plan itself requires `Decision::Prompt` before
  the engine starts executing — the user sees the plan and confirms.
- **Abort**: any task producing `is_error: true` with severity `CRITICAL` aborts
  the phase and skips remaining phases unless `continue_on_failure: true`.

---

## 8. Integration with MUSE/SkillOpt

Workflow plans are themselves candidates for Skill creation:

- After a successful workflow execution, the `workflow_manifest.json` + `plan.json`
  can be converted to a SKILL.md (MUSE pattern: create skill from experience).
- Example: "Security audit all crates" → `security-audit-workflow/SKILL.md` with
  the plan embedded as the workflow step.
- On the next run of the same task type, the skill retrieves the pre-built plan
  instead of the model re-generating it from scratch.

---

## 9. Phase-completion events (SQ/EQ)

```rust
pub enum Event {
    // ... existing events ...
    WorkflowStarted  { workflow_id: WorkflowId, plan_title: String, total_tasks: usize },
    PhaseStarted     { workflow_id: WorkflowId, phase_title: String, task_count: usize },
    TaskStarted      { workflow_id: WorkflowId, phase_title: String, task_id: String },
    TaskCompleted    { workflow_id: WorkflowId, phase_title: String, task_id: String,
                       score: Option<f64>, tokens: u32 },
    TaskFailed       { workflow_id: WorkflowId, phase_title: String, task_id: String,
                       error: String },
    PhaseCompleted   { workflow_id: WorkflowId, phase_title: String, succeeded: usize,
                       failed: usize },
    WorkflowCompleted{ workflow_id: WorkflowId, succeeded: usize, failed: usize,
                       elapsed_ms: u64 },
}
```

These events flow through the SQ/EQ bus to the TUI (progress display),
trace recorder, and gateway (Telegram/Slack status updates).
