//! Bash shell tool implementation.

use lamark_core::{ToolCall, ToolResult, TurnContext};

pub async fn run(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let command = call.arguments.get("command").and_then(|v| v.as_str());

    let command = match command {
        Some(c) => c.to_string(),
        None => return ToolResult::failure("Bash requires 'command' argument.".to_string()),
    };

    let timeout_ms = call.arguments.get("timeout").and_then(|v| v.as_u64());
    let background = call
        .arguments
        .get("run_in_background")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if background {
        let output = match std::process::Command::new("sh")
            .arg("-c")
            .arg(&command)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
        {
            Ok(mut child) => match child.try_wait() {
                Ok(Some(status)) => format!(
                    "Started and exited with status: {}",
                    status.code().unwrap_or(-1)
                ),
                Ok(None) => format!(
                    "Started (background PID: {}). No immediate output.",
                    child.id()
                ),
                Err(e) => format!("Error starting process: {e}"),
            },
            Err(e) => format!("Failed to start command: {e}"),
        };
        return ToolResult::success(output);
    }

    // Spawn and wait for output
    let command_clone = command.clone();
    let timeout_ms_cloned = timeout_ms;

    let result = if let Some(timeout_ms) = timeout_ms_cloned {
        // Use tokio::time::timeout with blocking task
        let timeout = std::time::Duration::from_millis(timeout_ms);
        tokio::time::timeout(timeout, async {
            tokio::task::spawn_blocking(move || {
                std::process::Command::new("sh")
                    .arg("-c")
                    .arg(&command_clone)
                    .stdout(std::process::Stdio::piped())
                    .stderr(std::process::Stdio::piped())
                    .output()
            })
            .await
        })
        .await
    } else {
        Ok(tokio::task::spawn_blocking(move || {
            std::process::Command::new("sh")
                .arg("-c")
                .arg(&command)
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .output()
        })
        .await)
    };

    let output = match result {
        Ok(Ok(Ok(output))) => output,
        Ok(Ok(Err(e))) => return ToolResult::failure(format!("Bash execution failed: {e}")),
        Ok(Err(e)) => return ToolResult::failure(format!("Bash execution failed: {e}")),
        Err(_) => return ToolResult::failure("Bash: command timed out.".to_string()),
    };

    let stdout_str = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr_str = String::from_utf8_lossy(&output.stderr).to_string();

    let mut output_parts = Vec::new();
    if !stdout_str.trim().is_empty() {
        output_parts.push(format!("stdout:\n{stdout_str}"));
    }
    if !stderr_str.trim().is_empty() {
        output_parts.push(format!("stderr:\n{stderr_str}"));
    }

    let output_text = if output_parts.is_empty() {
        "(empty)".to_string()
    } else {
        output_parts.join("\n")
    };

    ToolResult::success(format!(
        "Command executed (exit: {}). Output:\n{output_text}",
        output.status.code().unwrap_or(-1)
    ))
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
    async fn test_bash_missing_command() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(!result.success);
        assert!(result.output.contains("command"));
    }

    #[tokio::test]
    async fn test_bash_echo() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "echo hello"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("hello"));
    }

    #[tokio::test]
    async fn test_bash_exit_code() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "exit 42"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("exit: 42"));
    }

    #[tokio::test]
    async fn test_bash_stderr() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "echo error >&2"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("stderr"));
    }

    #[tokio::test]
    async fn test_bash_empty_output() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "true"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_bash_timeout() {
        // Test timeout with sleep that exceeds the limit
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({
                "command": "sleep 5",
                "timeout": 200, // 200ms — should time out
            }),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(!result.success);
        assert!(result.output.contains("timed out"));
    }

    #[tokio::test]
    async fn test_bash_background() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({
                "command": "sleep 10",
                "run_in_background": true,
            }),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
        // Background process should show PID or started message
        assert!(result.output.contains("PID"));
    }

    #[tokio::test]
    async fn test_bash_env_var() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "echo $HOME"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_bash_env_var_substitution() {
        let call = ToolCall {
            name: "Bash".into(),
            arguments: json!({"command": "X=hello; echo $X"}),
        };
        let ctx = test_ctx();
        let result = run(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("hello"));
    }
}
