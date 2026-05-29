//! `WorkflowId` and `WorkflowTrace` — links sub-rollout bundles.
//!
//! Each workflow execution gets a unique `WorkflowId`. All subagent trace
//! bundles produced during execution include this ID so the reducer can
//! re-link them into a single workflow-level trace.
//!
//! See `docs/plan/05e-dynamic-workflows.md §6`.

use serde::{Deserialize, Serialize};

/// Stable identifier for a single workflow execution.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct WorkflowId(uuid::Uuid);

impl WorkflowId {
    /// Generate a new workflow ID.
    pub fn new() -> Self {
        Self(uuid::Uuid::now_v7())
    }
}

impl Default for WorkflowId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for WorkflowId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "wf_{}", self.0)
    }
}

/// Top-level trace manifest written to `~/.lamark/traces/<workflow_id>/`.
#[derive(Debug, Serialize, Deserialize)]
pub struct WorkflowTrace {
    pub workflow_id: WorkflowId,
    pub plan_title: String,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub total_tasks: usize,
    pub succeeded: usize,
    pub failed: usize,
    /// Paths to sub-rollout directories (one per task).
    pub sub_rollouts: Vec<std::path::PathBuf>,
}
