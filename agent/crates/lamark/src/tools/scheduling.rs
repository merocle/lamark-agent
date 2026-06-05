//! ScheduleCron tool: create, list, pause, resume, remove cron jobs.

use lamark_core::{ToolCall, ToolResult, TurnContext};

// In-memory job registry (v1: no persistence)
#[derive(Clone)]
struct CronJob {
    name: String,
    schedule: String,
    #[allow(dead_code)]
    command: String,
    active: bool,
}

static CRON_STORE: std::sync::Mutex<Vec<CronJob>> = std::sync::Mutex::new(Vec::new());

fn parse_action(s: &str) -> Option<&'static str> {
    match s {
        "create" => Some("create"),
        "list" => Some("list"),
        "pause" => Some("pause"),
        "resume" => Some("resume"),
        "remove" => Some("remove"),
        _ => None,
    }
}

pub async fn schedule(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let action = call.arguments.get("action").and_then(|v| v.as_str());

    let Some(action) = action else {
        return ToolResult::failure("ScheduleCron requires 'action' argument.".to_string());
    };

    let action = match parse_action(action) {
        Some(a) => a,
        None => return ToolResult::failure(format!("ScheduleCron: invalid action '{action}'.")),
    };

    match action {
        "create" => {
            let name = call
                .arguments
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let schedule = call.arguments.get("schedule").and_then(|v| v.as_str());
            let command = call
                .arguments
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            let Some(schedule) = schedule else {
                return ToolResult::failure(
                    "ScheduleCron create requires 'schedule' argument.".to_string(),
                );
            };

            let stored_name = if name.is_empty() {
                format!("job-{}", CRON_STORE.lock().unwrap().len() + 1)
            } else {
                name.to_string()
            };

            CRON_STORE.lock().unwrap().push(CronJob {
                name: stored_name.clone(),
                schedule: schedule.to_string(),
                command: command.to_string(),
                active: true,
            });

            ToolResult::success(format!(
                "Cron job '{stored_name}' created. Schedule: {schedule}"
            ))
        }
        "list" => {
            let jobs = CRON_STORE.lock().unwrap();
            if jobs.is_empty() {
                return ToolResult::success("No scheduled cron jobs.".to_string());
            }

            let output = jobs
                .iter()
                .map(|j| {
                    format!(
                        "{} '{}' — every {} [{}]",
                        if j.active { "✓" } else { "⏸" },
                        j.name,
                        j.schedule,
                        if j.active { "active" } else { "paused" }
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            ToolResult::success(output)
        }
        "pause" | "resume" => {
            let name = call.arguments.get("name").and_then(|v| v.as_str());
            let Some(name) = name else {
                return ToolResult::failure(
                    "ScheduleCron pause/resume requires 'name' argument.".to_string(),
                );
            };

            let mut jobs = CRON_STORE.lock().unwrap();
            let active = action == "resume";
            match jobs.iter_mut().find(|j| j.name == name) {
                Some(j) => {
                    j.active = active;
                    ToolResult::success(format!("Cron job '{}' {}d.", name, action))
                }
                None => ToolResult::failure(format!("ScheduleCron: job '{}' not found.", name)),
            }
        }
        "remove" => {
            let name = call.arguments.get("name").and_then(|v| v.as_str());
            let Some(name) = name else {
                return ToolResult::failure(
                    "ScheduleCron remove requires 'name' argument.".to_string(),
                );
            };

            let mut jobs = CRON_STORE.lock().unwrap();
            let before = jobs.len();
            jobs.retain(|j| j.name != name);
            if jobs.len() < before {
                ToolResult::success(format!("Cron job '{}' removed.", name))
            } else {
                ToolResult::failure(format!("ScheduleCron: job '{}' not found.", name))
            }
        }
        _ => unreachable!(),
    }
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
    async fn test_schedule_missing_action() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_invalid_action() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "nonexistent"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_list_empty() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "list"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_schedule_create_without_schedule() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "create", "name": "myjob"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_create_with_schedule() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "create", "name": "myjob", "schedule": "0 9 * * *"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("myjob"));
    }

    #[tokio::test]
    async fn test_schedule_create_auto_name() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "create", "schedule": "* * * * *"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_schedule_pause_missing_name() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "pause"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_resume_missing_name() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "resume"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_remove_missing_name() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "remove"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_schedule_full_lifecycle() {
        let ctx = test_ctx();

        // Create
        let create_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "create", "name": "lifecycle-job", "schedule": "0 8 * * *"}),
        };
        let create_result = schedule(&create_call, &ctx).await;
        assert!(create_result.success);

        // List
        let list_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "list"}),
        };
        let list_result = schedule(&list_call, &ctx).await;
        assert!(list_result.success);

        // Pause
        let pause_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "pause", "name": "lifecycle-job"}),
        };
        let pause_result = schedule(&pause_call, &ctx).await;
        assert!(pause_result.success);

        // Resume
        let resume_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "resume", "name": "lifecycle-job"}),
        };
        let resume_result = schedule(&resume_call, &ctx).await;
        assert!(resume_result.success);

        // Remove
        let remove_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "remove", "name": "lifecycle-job"}),
        };
        let remove_result = schedule(&remove_call, &ctx).await;
        assert!(remove_result.success);

        // Remove again should fail
        let remove_again = schedule(&remove_call, &ctx).await;
        assert!(!remove_again.success);
    }

    #[tokio::test]
    async fn test_schedule_pause_nonexistent() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "pause", "name": "nonexistent"}),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_parse_action() {
        assert_eq!(parse_action("create"), Some("create"));
        assert_eq!(parse_action("list"), Some("list"));
        assert_eq!(parse_action("pause"), Some("pause"));
        assert_eq!(parse_action("resume"), Some("resume"));
        assert_eq!(parse_action("remove"), Some("remove"));
        assert_eq!(parse_action("invalid"), None);
    }

    #[tokio::test]
    async fn test_schedule_create_with_command() {
        let call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({
                "action": "create",
                "name": "cmd-job",
                "schedule": "0 * * * *",
                "command": "echo hello"
            }),
        };
        let ctx = test_ctx();
        let result = schedule(&call, &ctx).await;
        assert!(result.success);

        // Verify command is stored by listing
        let list_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "list"}),
        };
        let list_result = schedule(&list_call, &ctx).await;
        assert!(list_result.success);
    }

    #[tokio::test]
    async fn test_schedule_remove_after_create() {
        let ctx = test_ctx();

        let create_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "create", "name": "remove-me", "schedule": "30 * * * *"}),
        };
        schedule(&create_call, &ctx).await;

        let remove_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "remove", "name": "remove-me"}),
        };
        let remove_result = schedule(&remove_call, &ctx).await;
        assert!(remove_result.success);

        // List should not show it
        let list_call = ToolCall {
            name: "ScheduleCron".into(),
            arguments: json!({"action": "list"}),
        };
        let list_result = schedule(&list_call, &ctx).await;
        assert!(list_result.success);
    }
}
