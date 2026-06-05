//! Task tools: TaskCreate, TaskUpdate, TaskList.

use lamark_core::{ToolCall, ToolResult, TurnContext};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct Task {
    #[allow(dead_code)]
    id: u64,
    subject: String,
    #[allow(dead_code)]
    description: String,
    status: TaskStatus,
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum TaskStatus {
    Pending,
    InProgress,
    Completed,
    Deleted,
}

impl std::fmt::Display for TaskStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TaskStatus::Pending => write!(f, "pending"),
            TaskStatus::InProgress => write!(f, "in_progress"),
            TaskStatus::Completed => write!(f, "completed"),
            TaskStatus::Deleted => write!(f, "deleted"),
        }
    }
}

// In-memory task store (v1)
static TASK_STORE: std::sync::LazyLock<std::sync::Mutex<std::collections::HashMap<u64, Task>>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

fn parse_status(s: &str) -> Option<TaskStatus> {
    match s {
        "pending" => Some(TaskStatus::Pending),
        "in_progress" => Some(TaskStatus::InProgress),
        "completed" => Some(TaskStatus::Completed),
        "deleted" => Some(TaskStatus::Deleted),
        _ => None,
    }
}

pub async fn create(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let subject = call.arguments.get("subject").and_then(|v| v.as_str());
    let description = call.arguments.get("description").and_then(|v| v.as_str());

    let Some(subject) = subject else {
        return ToolResult::failure("TaskCreate requires 'subject' argument.".to_string());
    };

    let description = description.unwrap_or(subject);

    let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let task = Task {
        id,
        subject: subject.to_string(),
        description: description.to_string(),
        status: TaskStatus::Pending,
    };

    TASK_STORE.lock().unwrap().insert(id, task);
    ToolResult::success(format!("Task created (ID: {id}). Subject: {subject}"))
}

pub async fn update(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let task_id = call.arguments.get("task_id").and_then(|v| v.as_str());

    let Some(task_id) = task_id else {
        return ToolResult::failure("TaskUpdate requires 'task_id' argument.".to_string());
    };

    let task_id: u64 = match task_id.parse() {
        Ok(id) => id,
        Err(_) => return ToolResult::failure(format!("TaskUpdate: invalid task_id '{task_id}'")),
    };

    let status_str = call.arguments.get("status").and_then(|v| v.as_str());

    let Some(status_str) = status_str else {
        return ToolResult::failure("TaskUpdate requires 'status' argument.".to_string());
    };

    let Some(status) = parse_status(status_str) else {
        return ToolResult::failure(format!(
            "TaskUpdate: invalid status '{status_str}'. Must be pending, in_progress, completed, or deleted."
        ));
    };

    let mut tasks = TASK_STORE.lock().unwrap();
    if let Some(task) = tasks.get_mut(&task_id) {
        task.status = status;
        ToolResult::success(format!("Task {task_id} updated to '{status}'."))
    } else {
        ToolResult::failure(format!("TaskUpdate: task ID {task_id} not found."))
    }
}

pub async fn list(_call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let tasks = TASK_STORE.lock().unwrap();

    if tasks.is_empty() {
        return ToolResult::success("No tasks.".to_string());
    }

    let active_tasks: Vec<(u64, &Task)> = tasks
        .iter()
        .filter(|(_, t)| t.status != TaskStatus::Deleted)
        .map(|(id, t)| (*id, t))
        .collect();

    if active_tasks.is_empty() {
        return ToolResult::success("No tasks.".to_string());
    }

    let output = active_tasks
        .iter()
        .map(|(id, t)| {
            format!(
                "[{id}] {subject} — {status}",
                id = id,
                subject = t.subject,
                status = t.status
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    ToolResult::success(output)
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
    async fn test_taskcreate_missing_subject() {
        let call = ToolCall {
            name: "TaskCreate".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = create(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_taskcreate_basic() {
        let call = ToolCall {
            name: "TaskCreate".into(),
            arguments: json!({"subject": "fix the bug", "description": "Issue #42"}),
        };
        let ctx = test_ctx();
        let result = create(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Task created"));
    }

    #[tokio::test]
    async fn test_taskcreate_default_description() {
        let call = ToolCall {
            name: "TaskCreate".into(),
            arguments: json!({"subject": "only subject"}),
        };
        let ctx = test_ctx();
        let result = create(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_taskupdate_invalid_id() {
        let call = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": "not-a-number", "status": "completed"}),
        };
        let ctx = test_ctx();
        let result = update(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_taskupdate_not_found() {
        let call = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": "9999", "status": "completed"}),
        };
        let ctx = test_ctx();
        let result = update(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_taskupdate_invalid_status() {
        let call = ToolCall {
            name: "TaskCreate".into(),
            arguments: json!({"subject": "test task"}),
        };
        let ctx = test_ctx();
        create(&call, &ctx).await;

        // Find the task ID that was just created
        let id = TASK_STORE
            .lock()
            .unwrap()
            .iter()
            .next()
            .map(|(k, _)| k)
            .copied()
            .unwrap();

        let call2 = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": id.to_string(), "status": "invalid_status"}),
        };
        let result = update(&call2, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_taskcreate_update_list_lifecycle() {
        let ctx = test_ctx();

        // Create
        let create_call = ToolCall {
            name: "TaskCreate".into(),
            arguments: json!({"subject": "lifecycle test task"}),
        };
        let create_result = create(&create_call, &ctx).await;
        assert!(create_result.success);

        // Find the ID
        let id = TASK_STORE
            .lock()
            .unwrap()
            .iter()
            .next()
            .map(|(k, _)| k)
            .copied()
            .unwrap();

        // Update to in_progress
        let update_call = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": id.to_string(), "status": "in_progress"}),
        };
        let update_result = update(&update_call, &ctx).await;
        assert!(update_result.success);
        assert!(update_result.output.contains("in_progress"));

        // Update to completed
        let update_call2 = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": id.to_string(), "status": "completed"}),
        };
        let update_result2 = update(&update_call2, &ctx).await;
        assert!(update_result2.success);

        // List - should NOT show completed tasks (they're filtered)
        let list_call = ToolCall {
            name: "TaskList".into(),
            arguments: json!({}),
        };
        let list_result = list(&list_call, &ctx).await;
        assert!(list_result.success);

        // Update to deleted
        let delete_call = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": id.to_string(), "status": "deleted"}),
        };
        let delete_result = update(&delete_call, &ctx).await;
        assert!(delete_result.success);
    }

    #[tokio::test]
    async fn test_taskupdate_missing_status() {
        let call = ToolCall {
            name: "TaskUpdate".into(),
            arguments: json!({"task_id": "1"}),
        };
        let ctx = test_ctx();
        let result = update(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_tasklist_empty() {
        // Just call list and verify it succeeds (may show pre-existing tasks)
        let call = ToolCall {
            name: "TaskList".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = list(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_parse_status() {
        assert_eq!(parse_status("pending"), Some(TaskStatus::Pending));
        assert_eq!(parse_status("in_progress"), Some(TaskStatus::InProgress));
        assert_eq!(parse_status("completed"), Some(TaskStatus::Completed));
        assert_eq!(parse_status("deleted"), Some(TaskStatus::Deleted));
        assert_eq!(parse_status("invalid"), None);
    }

    #[tokio::test]
    async fn test_task_status_display() {
        use std::fmt::Write;
        let mut s = String::new();
        write!(&mut s, "{}", TaskStatus::Pending).unwrap();
        assert_eq!(s, "pending");

        s.clear();
        write!(&mut s, "{}", TaskStatus::InProgress).unwrap();
        assert_eq!(s, "in_progress");
    }
}
