//! Skill tools: SkillView, SkillList, SkillManage.

use lamark_core::{ToolCall, ToolResult, TurnContext};
use std::path::PathBuf;

fn resolve_skill_paths(ctx: &TurnContext<'_>) -> Vec<PathBuf> {
    let paths = ctx.config.skill_paths.clone();
    if paths.is_empty() {
        vec![PathBuf::from("~/.lamark/skills")]
    } else {
        paths
    }
}

fn find_skill_file(name: &str, paths: &[PathBuf]) -> Option<PathBuf> {
    for dir in paths {
        let base = dir.join(name);
        // Try various extensions
        for ext in &["md", "yaml", "yml", "txt"] {
            let candidate = base.with_extension(ext);
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }
    None
}

pub async fn view(call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
    let skill_name = call.arguments.get("skill_name").and_then(|v| v.as_str());

    let Some(skill_name) = skill_name else {
        return ToolResult::failure("SkillView requires 'skill_name' argument.".to_string());
    };

    let skill_paths = resolve_skill_paths(ctx);
    let file = match find_skill_file(skill_name, &skill_paths) {
        Some(f) => f,
        None => {
            return ToolResult::failure(format!(
                "SkillView: skill '{skill_name}' not found in {skill_paths:?}"
            ))
        }
    };

    match std::fs::read_to_string(&file) {
        Ok(content) => ToolResult::success(format!(
            "=== {name} ===\n(from {path})\n\n{content}",
            name = skill_name,
            path = file.display()
        )),
        Err(e) => ToolResult::failure(format!("SkillView failed: {e}")),
    }
}

pub async fn list(_call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
    let skill_paths = resolve_skill_paths(ctx);
    let mut skills = Vec::new();

    for dir in &skill_paths {
        if !dir.exists() {
            continue;
        }

        match std::fs::read_dir(dir) {
            Ok(entries) => {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if let Some(ext) = name.rsplit('.').next() {
                        if matches!(ext, "md" | "yaml" | "yml") && name != "." {
                            let skill_name = name.trim_end_matches(&format!(".{ext}")).to_string();
                            // Try to extract description from file content
                            let desc = std::fs::read_to_string(entry.path())
                                .ok()
                                .and_then(|c| {
                                    c.lines()
                                        .find(|l| l.starts_with("# ") || l.starts_with("## "))
                                        .map(|l| format!("# {}", l.trim_start_matches('#').trim()))
                                })
                                .unwrap_or_else(|| "No description".to_string());
                            skills.push(format!("- {skill_name}: {desc}"));
                        }
                    }
                }
            }
            Err(_) => {}
        }
    }

    if skills.is_empty() {
        ToolResult::success("No skills found.".to_string())
    } else {
        ToolResult::success(format!("Available skills:\n{}", skills.join("\n")))
    }
}

pub async fn manage(call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
    let action = call.arguments.get("action").and_then(|v| v.as_str());
    let name = call.arguments.get("name").and_then(|v| v.as_str());

    let (Some(action), Some(name)) = (action, name) else {
        return ToolResult::failure(
            "SkillManage requires 'action' and 'name' arguments.".to_string(),
        );
    };

    let content = call
        .arguments
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let skill_paths = resolve_skill_paths(ctx);
    let target_dir = skill_paths
        .first()
        .cloned()
        .unwrap_or_else(|| PathBuf::from("~/.lamark/skills"));

    match action {
        "create" => {
            if content.is_empty() {
                return ToolResult::failure(
                    "SkillManage create requires 'content' argument.".to_string(),
                );
            }
            let target = target_dir.join(name).with_extension("md");
            std::fs::create_dir_all(&target_dir).ok();
            if let Err(e) = std::fs::write(&target, content) {
                return ToolResult::failure(format!("SkillManage create failed: {e}"));
            }
            ToolResult::success(format!("Skill '{name}' created at {}", target.display()))
        }
        "update" => {
            let skill_paths = resolve_skill_paths(ctx);
            let file = match find_skill_file(name, &skill_paths) {
                Some(f) => f,
                None => {
                    return ToolResult::failure(format!(
                        "SkillManage update: skill '{name}' not found."
                    ))
                }
            };
            if content.is_empty() {
                return ToolResult::failure(
                    "SkillManage update requires 'content' argument.".to_string(),
                );
            }
            if let Err(e) = std::fs::write(&file, content) {
                return ToolResult::failure(format!("SkillManage update failed: {e}"));
            }
            ToolResult::success(format!("Skill '{name}' updated at {}", file.display()))
        }
        "delete" => {
            let skill_paths = resolve_skill_paths(ctx);
            let file = match find_skill_file(name, &skill_paths) {
                Some(f) => f,
                None => {
                    return ToolResult::failure(format!(
                        "SkillManage delete: skill '{name}' not found."
                    ))
                }
            };
            if let Err(e) = std::fs::remove_file(&file) {
                return ToolResult::failure(format!("SkillManage delete failed: {e}"));
            }
            ToolResult::success(format!("Skill '{name}' deleted."))
        }
        _ => ToolResult::failure(format!(
            "SkillManage: unknown action '{action}'. Must be create, update, or delete."
        )),
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
    async fn test_skillview_missing_name() {
        let call = ToolCall {
            name: "SkillView".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = view(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillview_not_found() {
        let call = ToolCall {
            name: "SkillView".into(),
            arguments: json!({"skill_name": "nonexistent_skill_xyz"}),
        };
        let ctx = test_ctx();
        let result = view(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skilllist_basic() {
        let call = ToolCall {
            name: "SkillList".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = list(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_missing_action() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({"name": "test_skill"}),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_missing_name() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({"action": "create"}),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_create_update_delete() {
        // Use a temp directory as skill path
        let tmp_dir = tempfile::tempdir().unwrap();
        let mut config = lamark_config::Config::default();
        config.skill_paths = vec![tmp_dir.path().to_path_buf()];

        let conversation = Conversation::default();
        let ctx = TurnContext {
            conversation: &conversation,
            config: Arc::new(config),
        };

        // Create skill
        let create_call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({
                "action": "create",
                "name": "test_skill",
                "content": "# Test Skill\n\nThis is a test skill for unit testing."
            }),
        };
        let create_result = manage(&create_call, &ctx).await;
        assert!(create_result.success);

        // View skill
        let view_call = ToolCall {
            name: "SkillView".into(),
            arguments: json!({"skill_name": "test_skill"}),
        };
        let view_result = view(&view_call, &ctx).await;
        assert!(view_result.success);
        assert!(view_result.output.contains("Test Skill"));

        // Update skill
        let update_call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({
                "action": "update",
                "name": "test_skill",
                "content": "# Updated Skill\n\nNew content for skill."
            }),
        };
        let update_result = manage(&update_call, &ctx).await;
        assert!(update_result.success);

        // Verify update via view
        let view_result2 = view(&view_call, &ctx).await;
        assert!(view_result2.success);
        assert!(view_result2.output.contains("Updated Skill"));

        // Delete skill
        let delete_call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({
                "action": "delete",
                "name": "test_skill"
            }),
        };
        let delete_result = manage(&delete_call, &ctx).await;
        assert!(delete_result.success);

        // Verify deletion
        let view_result3 = view(&view_call, &ctx).await;
        assert!(!view_result3.success);
    }

    #[tokio::test]
    async fn test_skillmanage_unknown_action() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({"action": "nonexistent", "name": "test"}),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_create_without_content() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({"action": "create", "name": "test"}),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_update_nonexistent() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({
                "action": "update",
                "name": "nonexistent_xyz_skill",
                "content": "content"
            }),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_skillmanage_delete_nonexistent() {
        let call = ToolCall {
            name: "SkillManage".into(),
            arguments: json!({"action": "delete", "name": "nonexistent_xyz_skill"}),
        };
        let ctx = test_ctx();
        let result = manage(&call, &ctx).await;
        assert!(!result.success);
    }

    fn find_skill_file_for_dir(dir: &std::path::Path) -> Option<std::path::PathBuf> {
        find_skill_file("test", &[dir.to_path_buf()])
    }

    #[tokio::test]
    async fn test_find_skill_file_no_match() {
        let tmp_dir = tempfile::tempdir().unwrap();
        assert!(find_skill_file_for_dir(tmp_dir.path()).is_none());
    }

    #[tokio::test]
    async fn test_find_skill_file_finds_md() {
        let tmp_dir = tempfile::tempdir().unwrap();
        std::fs::write(tmp_dir.path().join("test.md"), "content").unwrap();
        let result = find_skill_file_for_dir(tmp_dir.path());
        assert!(result.is_some());
        assert_eq!(result.unwrap().extension().unwrap(), "md");
    }

    #[tokio::test]
    async fn test_find_skill_file_prefers_md_over_yaml() {
        let tmp_dir = tempfile::tempdir().unwrap();
        std::fs::write(tmp_dir.path().join("test.yaml"), "content").unwrap();
        std::fs::write(tmp_dir.path().join("test.md"), "content").unwrap();
        let result = find_skill_file_for_dir(tmp_dir.path());
        assert!(result.is_some());
        assert_eq!(result.unwrap().extension().unwrap(), "md");
    }

    #[tokio::test]
    async fn test_resolve_skill_paths_default() {
        let config = lamark_config::Config::default();
        let ctx = TurnContext {
            conversation: &Conversation::default(),
            config: Arc::new(config),
        };
        let paths = resolve_skill_paths(&ctx);
        assert_eq!(paths.len(), 1);
        assert!(paths[0].to_str().unwrap().contains(".lamark/skills"));
    }

    #[tokio::test]
    async fn test_resolve_skill_paths_configured() {
        let tmp_dir = tempfile::tempdir().unwrap();
        let mut config = lamark_config::Config::default();
        config.skill_paths = vec![tmp_dir.path().to_path_buf()];

        let ctx = TurnContext {
            conversation: &Conversation::default(),
            config: Arc::new(config),
        };
        let paths = resolve_skill_paths(&ctx);
        assert_eq!(paths.len(), 1);
    }
}
