//! `workflow_plan` tool — the model calls this to submit a structured plan.
//!
//! See `docs/plan/05e-dynamic-workflows.md §4.3`.

use crate::plan::WorkflowPlan;
use serde::{Deserialize, Serialize};

/// The `workflow_plan` tool schema (registered in `ToolRegistry` at startup).
///
/// When the model calls this tool, the engine parses the arguments as a
/// `WorkflowPlan` and begins execution after user confirmation.
pub struct WorkflowPlanTool;

impl WorkflowPlanTool {
    /// JSON Schema for the `workflow_plan` tool arguments.
    ///
    /// Full implementation uses `schemars::schema_for!(WorkflowPlan)` once
    /// `WorkflowPlan` derives `JsonSchema`. Stub returns a minimal schema.
    pub fn schema() -> serde_json::Value {
        serde_json::json!({
            "type": "object",
            "description": "Generate an orchestration plan for a complex multi-step task. \
                            Use when the task requires parallel investigation across many items \
                            or synthesis of multiple independent sub-results.",
            "properties": {
                "title":       { "type": "string", "description": "Short plan title" },
                "description": { "type": "string" },
                "phases":      { "type": "array", "items": { "type": "object" } }
            },
            "required": ["title", "phases"]
        })
    }

    /// Parse a raw arguments string into a `WorkflowPlan`.
    pub fn parse_args(arguments: &str) -> lamark_core::Result<WorkflowPlan> {
        serde_json::from_str(arguments)
            .map_err(|e| lamark_core::Error::Other(format!("workflow_plan arg parse: {e}")))
    }
}
