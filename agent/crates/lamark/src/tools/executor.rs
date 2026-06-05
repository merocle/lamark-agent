//! Central tool executor dispatcher.

use super::{agents, file, interactive, memory, proxy, scheduling, shell, skills, task, web};
use lamark_core::{ToolCall, ToolExecutor, ToolResult, TurnContext};
use std::sync::Arc;

/// Default tool executor that dispatches all tool calls to their implementations.
pub struct DefaultExecutor {
    _config: Arc<lamark_config::Config>,
}

impl DefaultExecutor {
    pub fn new(config: Arc<lamark_config::Config>) -> Self {
        Self { _config: config }
    }
}

#[async_trait::async_trait]
impl ToolExecutor for DefaultExecutor {
    async fn execute(&self, call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
        match call.name.as_str() {
            "Read" => file::read(call, ctx).await,
            "Write" => file::write(call, ctx).await,
            "Edit" => file::edit(call, ctx).await,
            "Glob" => file::glob(call, ctx).await,
            "Grep" => file::grep(call, ctx).await,
            "Bash" => shell::run(call, ctx).await,
            "WebSearch" => web::search(call, ctx).await,
            "WebFetch" => web::fetch(call, ctx).await,
            "MemorySearch" => memory::search(call, ctx).await,
            "MemoryWrite" => memory::write(call, ctx).await,
            "UserProfileGet" => memory::profile_get(call, ctx).await,
            "TaskCreate" => task::create(call, ctx).await,
            "TaskUpdate" => task::update(call, ctx).await,
            "TaskList" => task::list(call, ctx).await,
            "SkillView" => skills::view(call, ctx).await,
            "SkillList" => skills::list(call, ctx).await,
            "SkillManage" => skills::manage(call, ctx).await,
            "Agent" => agents::agent_tool(call, ctx).await,
            "Kanban" => agents::kanban(call, ctx).await,
            "AskUserQuestion" => interactive::ask_user_question(call, ctx).await,
            "AskApproval" => interactive::ask_approval(call, ctx).await,
            "ScheduleCron" => scheduling::schedule(call, ctx).await,
            "MCPProxy" => proxy::mcp_proxy(call, ctx).await,
            "ToolSearch" => proxy::tool_search(call, ctx).await,
            "WorkflowPlan" => proxy::workflow_plan(call, ctx).await,
            "WorktreeCreate" => proxy::worktree_create(call, ctx).await,
            _ => ToolResult::failure(format!("Unknown tool: '{}'", call.name)),
        }
    }
}
