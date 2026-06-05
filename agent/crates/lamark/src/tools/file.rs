//! File tool implementations: Read, Write, Edit, Glob, Grep.

use lamark_core::{ToolCall, ToolResult, TurnContext};

pub async fn read(_call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let call = _call;
    let path = call.arguments.get("file_path").and_then(|v| v.as_str());
    let Some(path) = path else {
        return ToolResult::failure("Read requires 'file_path' argument.".to_string());
    };

    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => return ToolResult::failure(format!("Read failed: {e}")),
    };

    // Apply optional offset/limit (1-based start line)
    let offset = call
        .arguments
        .get("offset")
        .and_then(|v| v.as_u64())
        .unwrap_or(1) as usize;
    let limit = call
        .arguments
        .get("limit")
        .and_then(|v| v.as_u64())
        .map(|l| l as usize);

    let lines: Vec<&str> = content.lines().collect();
    if offset < 1 || offset > lines.len() {
        return ToolResult::success(content); // out of range, return full content
    }

    let start = offset - 1; // convert to 0-based
    let end = limit
        .map(|l| (start + l).min(lines.len()))
        .unwrap_or(lines.len());
    let selected: Vec<&str> = lines[start..end].to_vec();

    ToolResult::success(selected.join("\n"))
}

pub async fn write(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let path = call.arguments.get("file_path").and_then(|v| v.as_str());
    let content = call.arguments.get("content").and_then(|v| v.as_str());

    let path = match path {
        Some(p) => p,
        None => {
            return ToolResult::failure(
                "Write requires 'file_path' and 'content' arguments.".to_string(),
            )
        }
    };

    let content = match content {
        Some(c) => c,
        None => {
            return ToolResult::failure(
                "Write requires 'file_path' and 'content' arguments.".to_string(),
            )
        }
    };

    // Create parent directories if needed
    if let Some(parent) = std::path::Path::new(path).parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                return ToolResult::failure(format!("Write failed: {e}"));
            }
        }
    }

    if let Err(e) = std::fs::write(path, content) {
        return ToolResult::failure(format!("Write failed: {e}"));
    }

    ToolResult::success(format!(
        "Successfully wrote {} bytes to {path}",
        content.len()
    ))
}

pub async fn edit(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let path = call.arguments.get("file_path").and_then(|v| v.as_str());
    let old_string = call.arguments.get("old_string").and_then(|v| v.as_str());
    let new_string = call.arguments.get("new_string").and_then(|v| v.as_str());

    let path = match path {
        Some(p) => p,
        None => {
            return ToolResult::failure(
                "Edit requires 'file_path', 'old_string', and 'new_string' arguments.".to_string(),
            )
        }
    };

    let old_string = match old_string {
        Some(o) => o,
        None => {
            return ToolResult::failure(
                "Edit requires 'file_path', 'old_string', and 'new_string' arguments.".to_string(),
            )
        }
    };

    let new_string = match new_string {
        Some(n) => n,
        None => {
            return ToolResult::failure(
                "Edit requires 'file_path', 'old_string', and 'new_string' arguments.".to_string(),
            )
        }
    };

    let content = match std::fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => return ToolResult::failure(format!("Edit failed: {e}")),
    };

    let replace_all = call
        .arguments
        .get("replace_all")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if replace_all {
        let count = content.matches(old_string).count();
        if count == 0 {
            return ToolResult::failure("Edit failed: old_string not found in file.".to_string());
        }
        let new_content = content.replace(old_string, new_string);
        if let Err(e) = std::fs::write(path, &new_content) {
            return ToolResult::failure(format!("Edit failed: {e}"));
        }
        return ToolResult::success(format!(
            "Successfully replaced {count} occurrence(s) of text in {path}"
        ));
    }

    // Find first occurrence and verify uniqueness
    match content.find(old_string) {
        None => ToolResult::failure("Edit failed: old_string not found in file.".to_string()),
        Some(pos) => {
            // Check that it appears exactly once
            let occurrences = content.match_indices(old_string).count();
            if occurrences > 1 {
                return ToolResult::failure(
                    "Edit failed: old_string found multiple times — must be unique. Use replace_all=true.".to_string()
                );
            }

            let new_content =
                content[..pos].to_string() + new_string + &content[pos + old_string.len()..];
            if let Err(e) = std::fs::write(path, &new_content) {
                return ToolResult::failure(format!("Edit failed: {e}"));
            }
            ToolResult::success(format!("Successfully edited {path}"))
        }
    }
}

pub async fn glob(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let pattern = call.arguments.get("pattern").and_then(|v| v.as_str());
    let path = call
        .arguments
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or(".");

    let pattern = match pattern {
        Some(p) => p,
        None => return ToolResult::failure("Glob requires 'pattern' argument.".to_string()),
    };

    let search_path = format!("{path}/{pattern}");
    match glob::glob(&search_path) {
        Ok(entries) => {
            let paths: Vec<String> = entries
                .filter_map(|e| e.ok())
                .filter_map(|e| e.to_str().map(String::from))
                .collect();

            // Sort by modification time (newest first) if possible
            let mut path_times: Vec<(String, std::time::SystemTime)> = paths
                .iter()
                .filter_map(|p| {
                    std::fs::metadata(p)
                        .ok()
                        .and_then(|m| m.modified().ok())
                        .map(|t| (p.clone(), t))
                })
                .collect();
            path_times.sort_by(|a, b| b.1.cmp(&a.1));

            let result: Vec<String> = path_times.into_iter().map(|(p, _)| p).collect();
            if result.is_empty() {
                ToolResult::success("No files matched.".to_string())
            } else {
                ToolResult::success(result.join("\n"))
            }
        }
        Err(e) => ToolResult::failure(format!("Glob failed: {e}")),
    }
}

pub async fn grep(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let pattern = call.arguments.get("pattern").and_then(|v| v.as_str());

    let pattern = match pattern {
        Some(p) => p,
        None => return ToolResult::failure("Grep requires 'pattern' argument.".to_string()),
    };

    let path = call
        .arguments
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or(".");
    let output_mode = call.arguments.get("output_mode").and_then(|v| v.as_str());

    // Use ripgrep if available
    let output = match std::process::Command::new("rg")
        .args(["-n", "--with-filename"])
        .arg(pattern)
        .current_dir(path)
        .output()
    {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
        Ok(o) if o.status.code() == Some(1) => String::new(), // no matches
        Ok(o) => format!("Grep error: {}", String::from_utf8_lossy(&o.stderr)),
        Err(_) => String::new(), // best-effort fallback
    };

    match output_mode {
        Some("files_with_matches") => {
            let files: Vec<&str> = output.lines().filter_map(|l| l.split(':').next()).collect();
            ToolResult::success(files.join("\n"))
        }
        Some("count") => {
            let count = output.lines().filter(|l| !l.is_empty()).count();
            ToolResult::success(count.to_string())
        }
        _ => {
            if output.is_empty() {
                ToolResult::success("No matches found.".to_string())
            } else {
                ToolResult::success(output)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lamark_core::{Conversation, ToolCall, TurnContext};
    use serde_json::json;
    use std::fs;
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
    async fn test_read_missing_file_path() {
        let call = ToolCall {
            name: "Read".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = read(&call, &ctx).await;
        assert!(!result.success);
        assert!(result.output.contains("file_path"));
    }

    #[tokio::test]
    async fn test_read_existing_file() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "line1\nline2\nline3\n").unwrap();

        let call = ToolCall {
            name: "Read".into(),
            arguments: json!({"file_path": tmp.path().to_str().unwrap()}),
        };
        let ctx = test_ctx();
        let result = read(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(result.output, "line1\nline2\nline3");
    }

    #[tokio::test]
    async fn test_read_with_offset() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "a\nb\nc\nd\ne\n").unwrap();

        let call = ToolCall {
            name: "Read".into(),
            arguments: json!({"file_path": tmp.path().to_str().unwrap(), "offset": 2, "limit": 2}),
        };
        let ctx = test_ctx();
        let result = read(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(result.output, "b\nc");
    }

    #[tokio::test]
    async fn test_write_basic() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = tmp.path().to_str().unwrap().to_string();
        drop(tmp); // Remove temp file so write can create fresh

        let call = ToolCall {
            name: "Write".into(),
            arguments: json!({"file_path": &path, "content": "hello world"}),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("11 bytes"));
        assert_eq!(fs::read_to_string(&path).unwrap(), "hello world");
    }

    #[tokio::test]
    async fn test_write_missing_args() {
        let call = ToolCall {
            name: "Write".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_write_creates_dirs() {
        let tmp_dir = tempfile::tempdir().unwrap();
        let sub_path = tmp_dir.path().join("sub/deep/file.txt");
        let path_str = sub_path.to_str().unwrap();

        let call = ToolCall {
            name: "Write".into(),
            arguments: json!({"file_path": path_str, "content": "deep content"}),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(fs::read_to_string(&sub_path).unwrap(), "deep content");
    }

    #[tokio::test]
    async fn test_edit_basic() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "foo bar baz").unwrap();

        let call = ToolCall {
            name: "Edit".into(),
            arguments: json!({
                "file_path": tmp.path().to_str().unwrap(),
                "old_string": "bar",
                "new_string": "qux"
            }),
        };
        let ctx = test_ctx();
        let result = edit(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(fs::read_to_string(tmp.path()).unwrap(), "foo qux baz");
    }

    #[tokio::test]
    async fn test_edit_not_found() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "hello world").unwrap();

        let call = ToolCall {
            name: "Edit".into(),
            arguments: json!({
                "file_path": tmp.path().to_str().unwrap(),
                "old_string": "xyz",
                "new_string": "abc"
            }),
        };
        let ctx = test_ctx();
        let result = edit(&call, &ctx).await;
        assert!(!result.success);
        assert!(result.output.contains("not found"));
    }

    #[tokio::test]
    async fn test_edit_multiple_occurrences() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "cat bat rat").unwrap();

        let call = ToolCall {
            name: "Edit".into(),
            arguments: json!({
                "file_path": tmp.path().to_str().unwrap(),
                "old_string": "at",
                "new_string": "et"
            }),
        };
        let ctx = test_ctx();
        let result = edit(&call, &ctx).await;
        assert!(!result.success);
        assert!(result.output.contains("multiple times"));
    }

    #[tokio::test]
    async fn test_edit_replace_all() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        fs::write(tmp.path(), "cat bat rat").unwrap();

        let call = ToolCall {
            name: "Edit".into(),
            arguments: json!({
                "file_path": tmp.path().to_str().unwrap(),
                "old_string": "at",
                "new_string": "et",
                "replace_all": true
            }),
        };
        let ctx = test_ctx();
        let result = edit(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(fs::read_to_string(tmp.path()).unwrap(), "cet bet ret");
    }

    #[tokio::test]
    async fn test_glob_basic() {
        let tmp_dir = tempfile::tempdir().unwrap();
        fs::write(tmp_dir.path().join("a.rs"), "").unwrap();
        fs::write(tmp_dir.path().join("b.rs"), "").unwrap();
        fs::write(tmp_dir.path().join("c.txt"), "").unwrap();

        let path = tmp_dir.path().to_str().unwrap();
        let call = ToolCall {
            name: "Glob".into(),
            arguments: json!({"pattern": "**/*.rs", "path": path}),
        };
        let ctx = test_ctx();
        let result = glob(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_glob_no_match() {
        let tmp_dir = tempfile::tempdir().unwrap();
        let call = ToolCall {
            name: "Glob".into(),
            arguments: json!({"pattern": "**/*.xyz", "path": tmp_dir.path().to_str().unwrap()}),
        };
        let ctx = test_ctx();
        let result = glob(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(result.output, "No files matched.");
    }

    #[tokio::test]
    async fn test_grep_basic() {
        // Requires ripgrep to be installed
        let skip_without_rg = !std::process::Command::new("rg")
            .arg("--version")
            .output()
            .is_ok();
        if skip_without_rg {
            return;
        }

        let tmp_dir = tempfile::tempdir().unwrap();
        fs::write(
            tmp_dir.path().join("test.rs"),
            "fn hello() {}\nfn world() {}",
        )
        .unwrap();

        let path = tmp_dir.path().to_str().unwrap();
        let call = ToolCall {
            name: "Grep".into(),
            arguments: json!({"pattern": "fn hello", "path": path}),
        };
        let ctx = test_ctx();
        let result = grep(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_grep_files_with_matches() {
        let skip_without_rg = !std::process::Command::new("rg")
            .arg("--version")
            .output()
            .is_ok();
        if skip_without_rg {
            return;
        }

        let tmp_dir = tempfile::tempdir().unwrap();
        fs::write(tmp_dir.path().join("a.rs"), "fn foo() {}\n").unwrap();
        fs::write(tmp_dir.path().join("b.rs"), "fn bar() {}\n").unwrap();

        let path = tmp_dir.path().to_str().unwrap();
        let call = ToolCall {
            name: "Grep".into(),
            arguments: json!({"pattern": "fn ", "path": path, "output_mode": "files_with_matches"}),
        };
        let ctx = test_ctx();
        let result = grep(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_grep_count() {
        let skip_without_rg = !std::process::Command::new("rg")
            .arg("--version")
            .output()
            .is_ok();
        if skip_without_rg {
            return;
        }

        let tmp_dir = tempfile::tempdir().unwrap();
        fs::write(tmp_dir.path().join("x.rs"), "fn foo() {}\nfn bar() {}\n").unwrap();

        let path = tmp_dir.path().to_str().unwrap();
        let call = ToolCall {
            name: "Grep".into(),
            arguments: json!({"pattern": "fn ", "path": path, "output_mode": "count"}),
        };
        let ctx = test_ctx();
        let result = grep(&call, &ctx).await;
        assert!(result.success);
        assert_eq!(result.output.trim(), "2");
    }

    #[tokio::test]
    async fn test_grep_no_match() {
        let skip_without_rg = !std::process::Command::new("rg")
            .arg("--version")
            .output()
            .is_ok();
        if skip_without_rg {
            return;
        }

        let tmp_dir = tempfile::tempdir().unwrap();
        fs::write(tmp_dir.path().join("x.rs"), "fn hello() {}\n").unwrap();

        let path = tmp_dir.path().to_str().unwrap();
        let call = ToolCall {
            name: "Grep".into(),
            arguments: json!({"pattern": "no_match_xyz", "path": path}),
        };
        let ctx = test_ctx();
        let result = grep(&call, &ctx).await;
        assert!(result.success);
    }
}
