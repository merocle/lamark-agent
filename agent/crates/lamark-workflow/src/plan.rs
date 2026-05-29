//! `WorkflowPlan` and related types — the structured output the model generates.
//!
//! See `docs/plan/05e-dynamic-workflows.md §2` for the full schema.

use serde::{Deserialize, Serialize};

/// The top-level orchestration plan produced by one model call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowPlan {
    /// Short human-readable title shown in the progress display.
    pub title: String,
    /// Optional longer description of the plan's intent.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Ordered list of phases; each phase may contain parallel tasks.
    pub phases: Vec<WorkflowPhase>,
    /// Whether to abort the workflow if more than `max_failure_pct` tasks fail.
    #[serde(default = "default_abort_on_failure")]
    pub abort_on_failure: bool,
}

fn default_abort_on_failure() -> bool {
    true
}

/// A named group of tasks sharing an execution mode.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowPhase {
    /// Phase title shown in the progress tree (matches `meta.phases` in script).
    pub title: String,
    /// One-line description of what this phase does.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    /// How tasks within this phase relate to each other.
    #[serde(default)]
    pub mode: PhaseMode,
    /// The tasks in this phase.
    pub tasks: Vec<WorkflowTask>,
}

/// Execution mode within a phase.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PhaseMode {
    /// All tasks start simultaneously; no data sharing between them.
    /// Wall-clock = slowest single task.
    #[default]
    Parallel,
    /// Each task feeds the next; tasks start as soon as their input is ready.
    /// Wall-clock = critical path length.
    Pipeline,
    /// Tasks run one by one in order.
    /// Wall-clock = sum of all tasks.
    Sequential,
}

/// A single unit of work assigned to one subagent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowTask {
    /// Stable identifier within the workflow (used in `depends_on`).
    pub id: String,
    /// Short label shown in the progress display.
    pub label: String,
    /// Complete, self-contained prompt sent to the subagent.
    pub prompt: String,
    /// Optional JSON Schema for structured output.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schema: Option<serde_json::Value>,
    /// Override the subagent type (e.g. `"Explore"`, `"code-reviewer"`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_type: Option<String>,
    /// IDs this task must wait for before starting.
    /// Format: `"task:<id>"` or `"phase:<title>"`.
    #[serde(default)]
    pub depends_on: Vec<String>,
}
