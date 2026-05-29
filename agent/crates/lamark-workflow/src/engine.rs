//! `WorkflowEngine` — deterministic execution of a `WorkflowPlan`.
//!
//! See `docs/plan/05e-dynamic-workflows.md §3`.

use crate::{
    plan::{PhaseMode, WorkflowPhase, WorkflowPlan},
    trace::WorkflowId,
};
use std::{collections::HashMap, sync::Arc, time::Duration};

/// Result of a complete workflow execution.
#[derive(Debug)]
pub struct WorkflowResult {
    pub workflow_id: WorkflowId,
    pub succeeded: usize,
    pub failed: usize,
    pub phase_results: HashMap<String, Vec<TaskResult>>,
}

/// Result of a single task within a workflow.
#[derive(Debug)]
pub struct TaskResult {
    pub task_id: String,
    pub output: String,
    pub structured: Option<serde_json::Value>,
    pub tokens_used: u32,
    pub is_error: bool,
}

/// Configuration for `WorkflowEngine`.
#[derive(Debug, Clone)]
pub struct WorkflowEngineConfig {
    /// Maximum simultaneous subagents (default: 16, matching Claude Code cap).
    pub max_concurrency: usize,
    /// Abort the workflow if this fraction of tasks fail.
    pub max_failure_fraction: f64,
    /// Per-task execution timeout.
    pub task_timeout: Duration,
    /// Write phase results to KB after each phase completes.
    pub checkpoint_phases: bool,
}

impl Default for WorkflowEngineConfig {
    fn default() -> Self {
        Self {
            max_concurrency: 16,
            max_failure_fraction: 0.05,
            task_timeout: Duration::from_secs(300),
            checkpoint_phases: true,
        }
    }
}

/// Executes a `WorkflowPlan` by spawning subagents for each task.
///
/// Implemented by wiring `lamark-coordinator` + `lamark-sandbox` in the binary.
/// See `docs/plan/05e-dynamic-workflows.md §3`.
pub struct WorkflowEngine {
    pub config: WorkflowEngineConfig,
    // coordinator: Arc<dyn SubagentCoordinator>,  — injected at runtime
}

impl WorkflowEngine {
    /// Create an engine with default configuration.
    pub fn new() -> Self {
        Self {
            config: WorkflowEngineConfig::default(),
        }
    }

    /// Execute the plan. Returns after all phases complete or the workflow aborts.
    ///
    /// # Implementation note
    /// Full implementation requires the `lamark-coordinator` SubagentCoordinator
    /// trait, injected by the `lamark` binary at startup. This stub compiles but
    /// returns an unimplemented error at runtime.
    pub async fn execute(
        &self,
        _plan: WorkflowPlan,
        _workflow_id: WorkflowId,
    ) -> lamark_core::Result<WorkflowResult> {
        Err(lamark_core::Error::Other(
            "WorkflowEngine::execute not yet implemented — \
             awaiting lamark-coordinator SubagentCoordinator wiring"
                .into(),
        ))
    }
}

impl Default for WorkflowEngine {
    fn default() -> Self {
        Self::new()
    }
}
