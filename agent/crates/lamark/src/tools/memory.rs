//! Memory tools: MemorySearch, MemoryWrite, UserProfileGet.

use lamark_core::{ToolCall, ToolResult, TurnContext};

// In-memory memory store (v1: no persistence)
struct MemoryStore {
    entries: std::sync::Mutex<Vec<MemoryEntry>>,
}

#[derive(Debug)]
struct MemoryEntry {
    content: String,
    tags: Vec<String>,
}

impl Default for MemoryStore {
    fn default() -> Self {
        Self {
            entries: std::sync::Mutex::new(Vec::new()),
        }
    }
}

impl MemoryStore {
    fn search(&self, query: &str, limit: usize) -> Vec<String> {
        let entries = self.entries.lock().unwrap();
        let query_lower = query.to_lowercase();

        entries
            .iter()
            .filter(|e| {
                e.content.to_lowercase().contains(&query_lower)
                    || e.tags
                        .iter()
                        .any(|t| t.to_lowercase().contains(&query_lower))
            })
            .take(limit)
            .map(|e| {
                if e.tags.is_empty() {
                    e.content.clone()
                } else {
                    format!("[{}] {}", e.tags.join(", "), e.content)
                }
            })
            .collect()
    }

    fn write(&self, content: &str, tags: &[String]) -> String {
        let mut entries = self.entries.lock().unwrap();
        let tags: Vec<String> = if tags.is_empty() {
            vec![]
        } else {
            tags.iter().cloned().collect()
        };
        entries.push(MemoryEntry {
            content: content.to_string(),
            tags,
        });
        format!(
            "Memory written successfully. Total entries: {}",
            entries.len()
        )
    }

    #[allow(dead_code)]
    fn list(&self) -> String {
        let entries = self.entries.lock().unwrap();
        if entries.is_empty() {
            "No memories stored.".to_string()
        } else {
            entries
                .iter()
                .enumerate()
                .map(|(i, e)| {
                    if e.tags.is_empty() {
                        format!("{i}. {content}", content = e.content)
                    } else {
                        format!("[{}] {}", i, e.tags.join(", "))
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        }
    }
}

// Global memory store instance (shared across tool calls)
static MEMORY_STORE: std::sync::LazyLock<MemoryStore> =
    std::sync::LazyLock::new(MemoryStore::default);

pub async fn search(_call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let query = _call.arguments.get("query").and_then(|v| v.as_str());

    let Some(query) = query else {
        return ToolResult::failure("MemorySearch requires 'query' argument.".to_string());
    };

    let limit = _call
        .arguments
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(5) as usize;

    let results = MEMORY_STORE.search(query, limit);

    if results.is_empty() {
        ToolResult::success(format!(
            "MemorySearch for \"{query}\" — no results found. Try a different query."
        ))
    } else {
        ToolResult::success(results.join("\n"))
    }
}

pub async fn write(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let content = call.arguments.get("content").and_then(|v| v.as_str());

    let Some(content) = content else {
        return ToolResult::failure("MemoryWrite requires 'content' argument.".to_string());
    };

    let tags = call
        .arguments
        .get("tags")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str())
                .map(String::from)
                .collect::<Vec<_>>()
        });

    let tags = tags.unwrap_or_default();
    ToolResult::success(MEMORY_STORE.write(content, &tags))
}

pub async fn profile_get(_call: &ToolCall, ctx: &TurnContext<'_>) -> ToolResult {
    let config = &ctx.config;

    let identity = if config.agent.identity.is_empty() {
        "Not set".to_string()
    } else {
        config.agent.identity.clone()
    };

    let profile = format!(
        "Identity: {identity}\nSafety mode: {:?}\nProvider: {}\nModel: {}",
        config.agent.safety, config.model.provider, config.model.model_id
    );

    ToolResult::success(profile)
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

    // Clear the in-memory store for isolated tests. We use a reset approach
    // since MEMORY_STORE is static and shared across test runs.

    #[allow(dead_code)]
    fn write_to_store(content: &str, tags: Vec<String>) {
        MEMORY_STORE.write(content, &tags);
    }

    #[tokio::test]
    async fn test_memorysearch_missing_query() {
        let call = ToolCall {
            name: "MemorySearch".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = search(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_memorysearch_no_results() {
        let call = ToolCall {
            name: "MemorySearch".into(),
            arguments: json!({"query": "nonexistent_xyz_12345"}),
        };
        let ctx = test_ctx();
        let result = search(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("no results found"));
    }

    #[tokio::test]
    async fn test_memorywrite_basic() {
        let call = ToolCall {
            name: "MemoryWrite".into(),
            arguments: json!({"content": "user prefers Python over JS"}),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_memorywrite_with_tags() {
        let call = ToolCall {
            name: "MemoryWrite".into(),
            arguments: json!({
                "content": "team uses Rust",
                "tags": ["lang", "preference"]
            }),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(result.success);
    }

    #[tokio::test]
    async fn test_memorywrite_missing_content() {
        let call = ToolCall {
            name: "MemoryWrite".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = write(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_memorysearch_can_find_written() {
        let call = ToolCall {
            name: "MemoryWrite".into(),
            arguments: json!({"content": "unique memory search test entry 7742"}),
        };
        let ctx = test_ctx();
        write(&call, &ctx).await;

        let call2 = ToolCall {
            name: "MemorySearch".into(),
            arguments: json!({"query": "7742"}),
        };
        let result = search(&call2, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("7742"));
    }

    #[tokio::test]
    async fn test_profile_get() {
        let call = ToolCall {
            name: "UserProfileGet".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = profile_get(&call, &ctx).await;
        assert!(result.success);
        assert!(result.output.contains("Identity:") || result.output.contains("Provider:"));
    }

    #[tokio::test]
    async fn test_memory_store_search() {
        let store = MemoryStore::default();
        store.write("rust is fast", &["lang".into()]);
        store.write("testing tests", &[]);

        let results = store.search("rust", 5);
        assert_eq!(results.len(), 1);

        let results = store.search("tests", 5);
        assert_eq!(results.len(), 1);

        let results = store.search("nothing", 5);
        assert!(results.is_empty());
    }
}
