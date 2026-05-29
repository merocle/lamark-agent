//! `skill_create` tool — MUSE-style skill creation from agent experience.
//!
//! When the agent identifies a capability gap it invokes this tool to generate
//! a new SKILL.md (+ optional scripts/ + tests/). The skill is only registered
//! after its tests pass in the sandbox.
//!
//! See `docs/plan/10d-skillopt-life-harness.md §8.5`.

use crate::traits::{Tool, ToolCapabilities, ToolSchema};
use lamark_core::tool::{ToolCall, ToolResult};
use lamark_core::turn::TurnContext;

/// The `skill_create` tool stub.
///
/// Full implementation: write SKILL.md + scripts/ + tests/, then call
/// `lamark_skills::register::register_skill()` with the sandbox test runner.
pub struct SkillCreateTool {
    pub skills_dir: std::path::PathBuf,
}

impl Tool for SkillCreateTool {
    fn name(&self) -> &str {
        "skill_create"
    }

    fn schema(&self) -> ToolSchema {
        serde_json::json!({
            "type": "object",
            "description": "Create a new reusable skill from agent experience. \
                            Only use when existing skills do not cover the task.",
            "properties": {
                "name":        { "type": "string", "description": "kebab-case skill name" },
                "description": { "type": "string" },
                "when_to_use": { "type": "array", "items": { "type": "string" } },
                "workflow":    { "type": "string", "description": "step-by-step procedure" },
                "scripts":     { "type": "object", "description": "filename → content" },
                "tests":       { "type": "object", "description": "pytest filename → content" }
            },
            "required": ["name", "description", "when_to_use", "workflow"]
        })
    }

    fn capabilities(&self) -> ToolCapabilities {
        ToolCapabilities {
            is_read_only: false,
            is_destructive: false,
            ..Default::default()
        }
    }

    fn invoke<'a>(
        &'a self,
        call: &'a ToolCall,
        _ctx: &'a TurnContext<'_>,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = lamark_core::Result<ToolResult>> + Send + 'a>,
    > {
        let id = call.id.clone();
        Box::pin(async move {
            // TODO: implement (parse args, write SKILL.md + scripts/ + tests/, call register_skill)
            Err(lamark_core::Error::Other(
                "skill_create not yet implemented".into(),
            ))
        })
    }
}
