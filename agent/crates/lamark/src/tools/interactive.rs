//! Interactive tools: AskUserQuestion, AskApproval.

use lamark_core::{ToolCall, ToolResult, TurnContext};

pub async fn ask_user_question(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let question = call.arguments.get("question").and_then(|v| v.as_str());

    let Some(question) = question else {
        return ToolResult::failure("AskUserQuestion requires 'question' argument.".to_string());
    };

    let options = call
        .arguments
        .get("options")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>());

    let mut result = format!("QUESTION: {question}");
    if let Some(options) = options {
        if !options.is_empty() {
            result.push_str("\n\nOptions:\n");
            for (i, opt) in options.iter().enumerate() {
                result.push_str(&format!("  {}. {}", i + 1, opt));
            }
        }
    }

    result.push_str(
        "\n\n(No user input collected in this session. Forward to stdin for interactive use.)",
    );
    ToolResult::success(result)
}

pub async fn ask_approval(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let summary = call.arguments.get("summary").and_then(|v| v.as_str());

    let Some(summary) = summary else {
        return ToolResult::failure("AskApproval requires 'summary' argument.".to_string());
    };

    let action = call
        .arguments
        .get("action")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let mut result = format!("PENDING APPROVAL\n\nSummary: {summary}");
    if !action.is_empty() {
        result.push_str(&format!("\nAction: {action}"));
    }

    result.push_str(
        "\n\n(Approval not collected in this session. Forward to stdin for interactive use.)",
    );
    ToolResult::success(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use lamark_core::{Conversation, ToolCall, TurnContext};
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
    async fn test_askuserquestion_missing_question() {
        let call = ToolCall {
            name: "AskUserQuestion".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = ask_user_question(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_askuserquestion_basic() {
        let call = ToolCall {
            name: "AskUserQuestion".into(),
            arguments: json!({"question": "What should I do?"}),
        };
        let ctx = test_ctx();
        let result = ask_user_question(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("QUESTION"));
    }

    #[tokio::test]
    async fn test_askuserquestion_with_options() {
        let call = ToolCall {
            name: "AskUserQuestion".into(),
            arguments: json!({
                "question": "Pick one",
                "options": ["a", "b", "c"]
            }),
        };
        let ctx = test_ctx();
        let result = ask_user_question(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Options:"));
        assert!(result.output.contains("a"));
        assert!(result.output.contains("b"));
    }

    #[tokio::test]
    async fn test_askuserquestion_empty_options() {
        let call = ToolCall {
            name: "AskUserQuestion".into(),
            arguments: json!({
                "question": "Pick one",
                "options": []
            }),
        };
        let ctx = test_ctx();
        let result = ask_user_question(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_askapproval_missing_summary() {
        let call = ToolCall {
            name: "AskApproval".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = ask_approval(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_askapproval_basic() {
        let call = ToolCall {
            name: "AskApproval".into(),
            arguments: json!({"summary": "Deploying v2.0"}),
        };
        let ctx = test_ctx();
        let result = ask_approval(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("PENDING APPROVAL"));
    }

    #[tokio::test]
    async fn test_askapproval_with_action() {
        let call = ToolCall {
            name: "AskApproval".into(),
            arguments: json!({"summary": "Deploy v2", "action": "deploy"}),
        };
        let ctx = test_ctx();
        let result = ask_approval(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Action: deploy"));
    }
}
