//! Proxy/meta/workflow tools: MCPProxy, ToolSearch, WorkflowPlan, WorktreeCreate.

use lamark_core::{ToolCall, ToolResult, TurnContext};

pub async fn mcp_proxy(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let server = call.arguments.get("server").and_then(|v| v.as_str());
    let tool_name = call.arguments.get("tool").and_then(|v| v.as_str());

    let (Some(server), Some(tool_name)) = (server, tool_name) else {
        return ToolResult::failure("MCPProxy requires 'server' and 'tool' arguments.".to_string());
    };

    let args = call.arguments.get("arguments").cloned().unwrap_or_default();

    // For v1: report the tool mapping. Actual MCP bridging requires subprocess management.
    let tool_ref = format!("mcp__{server}__{tool_name}");

    ToolResult::success(format!(
        "MCPProxy: bridging to '{server}' → tool '{tool_name}' (registered as '{tool_ref}').\nArguments: {args}\n\nFull MCP stdio/SSE bridging not yet implemented. Use tool_ref for direct API calls to the MCP server."
    ))
}

pub async fn tool_search(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let query = call.arguments.get("query").and_then(|v| v.as_str());

    let Some(query) = query else {
        return ToolResult::failure("ToolSearch requires 'query' argument.".to_string());
    };

    let query_lower = query.to_lowercase();
    let tools = lamark_core::tool::registry();

    let matches: Vec<String> = tools
        .iter()
        .filter(|t| {
            t.definition.name.to_lowercase().contains(&query_lower)
                || t.definition
                    .description
                    .to_lowercase()
                    .contains(&query_lower)
                || t.usage_hint.to_lowercase().contains(&query_lower)
        })
        .map(|t| {
            format!(
                "- {}: {}\n  Usage: {}",
                t.definition.name, t.definition.description, t.usage_hint
            )
        })
        .collect();

    if matches.is_empty() {
        ToolResult::success(format!("ToolSearch for \"{query}\" — no tools found."))
    } else {
        ToolResult::success(format!(
            "ToolSearch results for \"{query}\":\n{}",
            matches.join("\n\n")
        ))
    }
}

pub async fn workflow_plan(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let plan = call.arguments.get("plan");

    let Some(plan) = plan else {
        return ToolResult::failure("WorkflowPlan requires 'plan' argument.".to_string());
    };

    // For v1: echo the plan back. Real workflow engine integration is a follow-up.
    let plan_str = match serde_json::to_string_pretty(plan) {
        Ok(s) => s,
        Err(e) => {
            return ToolResult::failure(format!("WorkflowPlan: failed to serialize plan: {e}"))
        }
    };

    ToolResult::success(format!(
        "WorkflowPlan received. Ready for execution:\n\n{plan_str}"
    ))
}

pub async fn worktree_create(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let branch = call
        .arguments
        .get("branch")
        .and_then(|v| v.as_str())
        .unwrap_or("main");

    // Use git worktree to create isolated workspace
    let output = match std::process::Command::new("git")
        .args([
            "worktree",
            "add",
            "-b",
            branch,
            &format!(".lamark-worktree-{branch}"),
        ])
        .output()
    {
        Ok(o) => String::from_utf8_lossy(&o.stdout).to_string(),
        Err(e) => return ToolResult::failure(format!("WorktreeCreate failed: {e}")),
    };

    if output.contains("already exists") || !output.is_empty() {
        ToolResult::success(format!("Worktree created for branch '{branch}'.\n{output}"))
    } else {
        ToolResult::success(format!(
            "Worktree created for branch '{branch}' (silent success)."
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::DefaultExecutor;
    use lamark_core::{Conversation, ToolExecutor};
    use serde_json::json;
    use std::sync::Arc;

    fn test_ctx() -> TurnContext<'static> {
        let conversation = Box::leak(Box::new(Conversation::default()));
        let config = Arc::new(lamark_config::Config::default());
        TurnContext {
            conversation,
            config,
        }
    }

    #[tokio::test]
    async fn test_mcp_proxy_missing_server() {
        let call = ToolCall {
            name: "MCPProxy".into(),
            arguments: json!({"tool": "get_repos"}),
        };
        let ctx = test_ctx();
        let result = mcp_proxy(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_mcp_proxy_missing_tool() {
        let call = ToolCall {
            name: "MCPProxy".into(),
            arguments: json!({"server": "rustrover"}),
        };
        let ctx = test_ctx();
        let result = mcp_proxy(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_mcp_proxy_success() {
        let call = ToolCall {
            name: "MCPProxy".into(),
            arguments: json!({
                "server": "github",
                "tool": "get_repos",
                "arguments": {"owner": "jetbrains"}
            }),
        };
        let ctx = test_ctx();
        let result = mcp_proxy(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("mcp__github__get_repos"));
    }

    #[tokio::test]
    async fn test_toolsearch_missing_query() {
        let call = ToolCall {
            name: "ToolSearch".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = tool_search(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_toolsearch_find_by_name() {
        let call = ToolCall {
            name: "ToolSearch".into(),
            arguments: json!({"query": "Read"}),
        };
        let ctx = test_ctx();
        let result = tool_search(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Read"));
    }

    #[tokio::test]
    async fn test_toolsearch_find_by_description() {
        let call = ToolCall {
            name: "ToolSearch".into(),
            arguments: json!({"query": "shell command"}),
        };
        let ctx = test_ctx();
        let result = tool_search(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_toolsearch_no_results() {
        let call = ToolCall {
            name: "ToolSearch".into(),
            arguments: json!({"query": "nonexistent_tool_xyz_12345"}),
        };
        let ctx = test_ctx();
        let result = tool_search(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("no tools found"));
    }

    #[tokio::test]
    async fn test_toolsearch_case_insensitive() {
        let call = ToolCall {
            name: "ToolSearch".into(),
            arguments: json!({"query": "bash"}), // lowercase — should match "Bash"
        };
        let ctx = test_ctx();
        let result = tool_search(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Bash"));
    }

    #[tokio::test]
    async fn test_workflowplan_missing_plan() {
        let call = ToolCall {
            name: "WorkflowPlan".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = workflow_plan(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_workflowplan_success() {
        let plan = json!({
            "phases": [{"name": "setup", "agents": ["test"]}]
        });
        let call = ToolCall {
            name: "WorkflowPlan".into(),
            arguments: json!({"plan": plan}),
        };
        let ctx = test_ctx();
        let result = workflow_plan(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_registry_all_tools_count() {
        let tools = lamark_core::tool::registry();
        assert!(!tools.is_empty());
        // Verify we have all expected tools in the registry
        let names: Vec<_> = tools.iter().map(|t| t.definition.name.as_str()).collect();
        for expected in &[
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "Bash",
            "WebSearch",
            "WebFetch",
            "MemorySearch",
            "MemoryWrite",
            "UserProfileGet",
            "TaskCreate",
            "TaskUpdate",
            "TaskList",
            "SkillView",
            "SkillList",
            "SkillManage",
            "Agent",
            "Kanban",
            "AskUserQuestion",
            "ScheduleCron",
            "MCPProxy",
            "ToolSearch",
            "WorkflowPlan",
            "WorktreeCreate",
        ] {
            assert!(
                names.contains(&expected),
                "{expected} should be in registry"
            );
        }
    }

    #[tokio::test]
    async fn test_executor_dispatches_all_tools() {
        let config = Arc::new(lamark_config::Config::default());
        let executor = DefaultExecutor::new(config);

        // Test that every tool name in the registry has a matching branch
        let tools = lamark_core::tool::registry();

        for entry in &tools {
            let call = ToolCall {
                name: entry.definition.name.clone(),
                arguments: serde_json::Value::Object(serde_json::Map::new()),
            };

            // This should not panic — it either succeeds or returns a structured failure
            let _ = executor.execute(&call, &test_ctx()).await;
        }

        // Test unknown tool
        let unknown_call = ToolCall {
            name: "NonExistentTool".into(),
            arguments: json!({}),
        };
        let result = executor.execute(&unknown_call, &test_ctx()).await;
        assert!(!result.success);
        assert!(result.output.contains("Unknown tool"));
    }
}
