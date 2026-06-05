//! Central tool executor dispatcher.

use super::{agents, file, interactive, memory, proxy, scheduling, shell, skills, task, web};
use lamark_core::{ToolCall, ToolExecutor, ToolResult, TurnContext};
use std::sync::Arc;

/// Default tool executor that dispatches all tool calls to their implementations.
pub struct DefaultExecutor {
    config: Arc<lamark_config::Config>,
}

impl DefaultExecutor {
    pub fn new(config: Arc<lamark_config::Config>) -> Self {
        Self { config }
    }

    /// Execute a single tool call (internal dispatch).
    async fn dispatch_owned(call: ToolCall, config: Arc<lamark_config::Config>) -> ToolResult {
        let ctx = TurnContext::new_owned(config);
        match call.name.as_str() {
            "Read" => file::read(&call, &ctx).await,
            "Write" => file::write(&call, &ctx).await,
            "Edit" => file::edit(&call, &ctx).await,
            "Glob" => file::glob(&call, &ctx).await,
            "Grep" => file::grep(&call, &ctx).await,
            "Bash" => shell::run(&call, &ctx).await,
            "WebSearch" => web::search(&call, &ctx).await,
            "WebFetch" => web::fetch(&call, &ctx).await,
            "MemorySearch" => memory::search(&call, &ctx).await,
            "MemoryWrite" => memory::write(&call, &ctx).await,
            "UserProfileGet" => memory::profile_get(&call, &ctx).await,
            "TaskCreate" => task::create(&call, &ctx).await,
            "TaskUpdate" => task::update(&call, &ctx).await,
            "TaskList" => task::list(&call, &ctx).await,
            "SkillView" => skills::view(&call, &ctx).await,
            "SkillList" => skills::list(&call, &ctx).await,
            "SkillManage" => skills::manage(&call, &ctx).await,
            "Agent" => agents::agent_tool(&call, &ctx).await,
            "Kanban" => agents::kanban(&call, &ctx).await,
            "AskUserQuestion" => interactive::ask_user_question(&call, &ctx).await,
            "AskApproval" => interactive::ask_approval(&call, &ctx).await,
            "ScheduleCron" => scheduling::schedule(&call, &ctx).await,
            "MCPProxy" => proxy::mcp_proxy(&call, &ctx).await,
            "ToolSearch" => proxy::tool_search(&call, &ctx).await,
            "WorkflowPlan" => proxy::workflow_plan(&call, &ctx).await,
            "WorktreeCreate" => proxy::worktree_create(&call, &ctx).await,
            _ => ToolResult::failure(format!("Unknown tool: '{}'", call.name)),
        }
    }

    /// Apply per-result size cap based on config.
    fn cap_result(&self, mut result: ToolResult) -> ToolResult {
        let limit = self.config.max_result_bytes;
        if result.output.len() > limit {
            result.output.truncate(limit);
            result
                .output
                .push_str("\n\n[TRUNCATED: output exceeded configured limit]\n");
            result
                .metadata
                .insert("truncated".to_string(), "true".to_string());
        }
        result
    }
}

#[async_trait::async_trait]
impl ToolExecutor for DefaultExecutor {
    async fn execute(&self, call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
        let owned_call = ToolCall {
            name: call.name.clone(),
            arguments: call.arguments.clone(),
        };
        Self::dispatch_owned(owned_call, ctx.config.clone()).await
    }

    async fn execute_batch(&self, calls: &[ToolCall], _ctx: &TurnContext<'_>) -> Vec<ToolResult> {
        let config = self.config.clone();

        // Enforce max tool calls per turn
        let limit = if calls.len() > self.config.max_tool_calls {
            self.config.max_tool_calls
        } else {
            calls.len()
        };

        let blocking_msg = format!(
            "Tool call limit reached: requested {} but maximum is {}",
            calls.len(),
            self.config.max_tool_calls
        );

        let mut results = Vec::with_capacity(calls.len());

        // Spawn concurrent tasks for approved calls
        let mut handles = Vec::with_capacity(limit);
        for (idx, call) in calls.iter().enumerate().take(limit) {
            let config = config.clone();
            let owned_call = ToolCall {
                name: call.name.clone(),
                arguments: call.arguments.clone(),
            };
            let handle = tokio::spawn(Self::dispatch_owned(owned_call, config));
            handles.push((idx, handle));
        }

        // Wait for results, preserving order
        for (orig_idx, handle) in handles {
            let result = match tokio::time::timeout(
                std::time::Duration::from_millis(self.config.tool_call_timeout_ms),
                handle,
            )
            .await
            {
                Ok(Ok(tool_result)) => tool_result,
                _ => ToolResult::failure("timed out".to_string()),
            };
            let capped = self.cap_result(result);
            results.resize(orig_idx + 1, capped);
        }

        // Fill remaining slots with blocking messages (for calls beyond limit)
        if limit < calls.len() {
            results.resize_with(calls.len(), || ToolResult::failure(blocking_msg.clone()));
        }

        results
    }
}
