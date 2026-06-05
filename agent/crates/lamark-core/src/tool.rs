//! Tool definitions and runtime dispatch for Lamark.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// A request from the LLM to execute a tool.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub name: String,
    #[serde(default)]
    pub arguments: serde_json::Value,
}

/// A formal tool schema describing what the LLM can invoke.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub parameters: serde_json::Value,
}

/// The result of a tool execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub output: String,
    pub success: bool,
    /// ID from the original ToolCall (used by OpenAI-compatible endpoints).
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

impl ToolResult {
    pub fn success(output: impl Into<String>) -> Self {
        Self {
            output: output.into(),
            success: true,
            metadata: HashMap::new(),
        }
    }

    pub fn failure(message: impl Into<String>) -> Self {
        Self {
            output: message.into(),
            success: false,
            metadata: HashMap::new(),
        }
    }
}

/// Registry entry: tool definition paired with a label for the system prompt.
pub struct ToolEntry {
    pub definition: ToolDefinition,
    pub usage_hint: String,
}

/// Returns all adopt-v0.1 tool definitions with their system-prompt hints.
pub fn registry() -> Vec<ToolEntry> {
    vec![
        ToolEntry {
            definition: ToolDefinition {
                name: "Read".into(),
                description: "Read a file from the workspace, optionally a line range.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Absolute path to the file."},
                        "offset": {"type": "integer", "description": "1-based start line."},
                        "limit": {"type": "integer", "description": "Max lines to read."}
                    },
                    "required": ["file_path"]
                }),
            },
            usage_hint: "Read(file_path, offset=1, limit) - file contents as string. Use for inspecting code, configs, and documentation.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Write".into(),
                description: "Create or overwrite a file.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Absolute path to write."},
                        "content": {"type": "string", "description": "Full file contents."}
                    },
                    "required": ["file_path", "content"]
                }),
            },
            usage_hint: "Write(file_path, content) - writes bytes to disk. Use for creating new files or replacing existing ones.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Edit".into(),
                description: "Exact-string replacement in a file; old_string must be unique."
                    .into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "old_string": {"type": "string", "description": "Exact text to replace; must be unique in the file."},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "description": "Replace every occurrence."}
                    },
                    "required": ["file_path", "old_string", "new_string"]
                }),
            },
            usage_hint: "Edit(file_path, old_string, new_string[, replace_all]) - replaces text. For targeted edits that preserve surrounding code.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Glob".into(),
                description: "Find files by glob pattern, sorted by modification time.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob like **/*.rs"},
                        "path": {"type": "string", "description": "Directory to search."}
                    },
                    "required": ["pattern"]
                }),
            },
            usage_hint: "Glob(pattern, path) - list of file paths matching the glob. Use to locate files before reading or editing.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Grep".into(),
                description: "Search file contents with a regular expression.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression."},
                        "path": {"type": "string"},
                        "glob": {"type": "string", "description": "Filter files by glob."},
                        "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]}
                    },
                    "required": ["pattern"]
                }),
            },
            usage_hint: "Grep(pattern, path, glob?, output_mode) - lines or files matching the regex. Use to find symbols, usages, and definitions.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Bash".into(),
                description:
                    "Execute a shell command in the sandbox; gated by the permission policy.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Timeout in milliseconds."},
                        "run_in_background": {"type": "boolean"}
                    },
                    "required": ["command"]
                }),
            },
            usage_hint: "Bash(command[, timeout, run_in_background]) - captures stdout/stderr. Use for build commands, tests, and system inspection.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "WebSearch".into(),
                description: "Search the web and return ranked results.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5}
                    },
                    "required": ["query"]
                }),
            },
            usage_hint: "WebSearch(query, limit) - ranked search results with URLs and snippets. Use when you need information not in your training data.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "WebFetch".into(),
                description: "Fetch a URL and extract its content as markdown.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "prompt": {"type": "string", "description": "What to extract or summarize."}
                    },
                    "required": ["url"]
                }),
            },
            usage_hint: "WebFetch(url, prompt?) - markdown content from a webpage. Use to read documentation or articles referenced in search results.".into(),
        },
    ]
}
