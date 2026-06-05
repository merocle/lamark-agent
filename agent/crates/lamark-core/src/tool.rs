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

/// Returns all adopt-v0.1 tool definitions from the YAML catalog with their system-prompt hints.
pub fn registry() -> Vec<ToolEntry> {
    vec![
        // ──────────────────── FILE ────────────────────
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
                description: "Create or overwrite a file; surfaces LSP/compiler diagnostics on completion.".into(),
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
                description: "Exact-string replacement in a file; old_string must be unique.".into(),
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
                description: "Search file contents with a regular expression (ripgrep-based).".into(),
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

        // ──────────────────── SHELL ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "Bash".into(),
                description: "Execute a shell command in the sandbox; gated by the permission policy.".into(),
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

        // ──────────────────── WEB ────────────────────
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

        // ──────────────────── MEMORY ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "MemorySearch".into(),
                description: "Search long-term memory in the knowledge-base over HTTP.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5}
                    },
                    "required": ["query"]
                }),
            },
            usage_hint: "MemorySearch(query, limit) - searches persistent memory. Use to recall past context, user preferences, and learned facts.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "MemoryWrite".into(),
                description: "Write a memory fact to the knowledge-base.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["content"]
                }),
            },
            usage_hint: "MemoryWrite(content, tags) - persists a memory fact. Use to store user preferences, project conventions, and learned patterns.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "UserProfileGet".into(),
                description: "Retrieve the current user profile snapshot.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {}
                }),
            },
            usage_hint: "UserProfileGet() - retrieves the user profile snapshot. Use to learn about the user before starting tasks.".into(),
        },

        // ──────────────────── TASK ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "TaskCreate".into(),
                description: "Create a task in the structured task list.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["subject", "description"]
                }),
            },
            usage_hint: "TaskCreate(subject, description) - adds a task to the structured task list. Use for multi-step work.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "TaskUpdate".into(),
                description: "Update a task's status or fields.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}
                    },
                    "required": ["task_id"]
                }),
            },
            usage_hint: "TaskUpdate(task_id, status) - changes a task's status (pending, in_progress, completed, deleted).".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "TaskList".into(),
                description: "List current tasks.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {}
                }),
            },
            usage_hint: "TaskList() - lists the current tasks in the task list.".into(),
        },

        // ──────────────────── SKILLS ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "SkillView".into(),
                description: "Read a skill document by name.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {"skill_name": {"type": "string"}},
                    "required": ["skill_name"]
                }),
            },
            usage_hint: "SkillView(skill_name) - reads a skill document by name. Use to learn project-specific workflows and conventions.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "SkillList".into(),
                description: "List available skills (name + description catalog).".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {}
                }),
            },
            usage_hint: "SkillList() - lists available skills. Use to discover what skills are installed and their capabilities.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "SkillManage".into(),
                description: "Create, update, or delete a skill document (validation-gated).".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "update", "delete"]},
                        "name": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["action", "name"]
                }),
            },
            usage_hint: "SkillManage(action, name, content?) - creates, updates, or deletes a skill document. Validation-gated.".into(),
        },

        // ──────────────────── AGENTS / COORDINATION ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "Agent".into(),
                description: "Spawn a subagent with an isolated context; only its final summary returns.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Short task title."},
                        "prompt": {"type": "string", "description": "The task for the subagent."},
                        "subagent_type": {"type": "string"}
                    },
                    "required": ["description", "prompt"]
                }),
            },
            usage_hint: "Agent(description, prompt[, subagent_type]) - spawns a subagent with isolated context; only the summary returns.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "Kanban".into(),
                description: "Multi-agent Kanban coordination (create/show/list/complete/block/comment/heartbeat/link).".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "show", "list", "complete", "block", "unblock", "comment", "heartbeat", "link"]},
                        "card_id": {"type": "string"},
                        "title": {"type": "string"}
                    },
                    "required": ["action"]
                }),
            },
            usage_hint: "Kanban(action[, card_id, title]) - multi-agent Kanban coordination. Use for parallel task management.".into(),
        },

        // ──────────────────── INTERACTIVE ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "AskUserQuestion".into(),
                description: "Ask the user a multiple-choice or open-ended clarifying question.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["question"]
                }),
            },
            usage_hint: "AskUserQuestion(question[, options]) - asks the user a clarifying question. Use when you need input before proceeding.".into(),
        },

        // ──────────────────── SCHEDULING ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "ScheduleCron".into(),
                description: "Create or manage scheduled jobs (cron).".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "list", "pause", "resume", "remove"]},
                        "name": {"type": "string"},
                        "schedule": {"type": "string", "description": "Cron expression."}
                    },
                    "required": ["action"]
                }),
            },
            usage_hint: "ScheduleCron(action, name?, schedule?) - creates and manages scheduled jobs.".into(),
        },

        // ──────────────────── MCP / META / WORKFLOW / WORKTREE ────────────────────
        ToolEntry {
            definition: ToolDefinition {
                name: "MCPProxy".into(),
                description: "Bridge to external MCP servers; their tools appear as mcp__<server>__<tool>.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "tool": {"type": "string"},
                        "arguments": {"type": "object"}
                    },
                    "required": ["server", "tool"]
                }),
            },
            usage_hint: "MCPProxy(server, tool, arguments?) - bridges external MCP servers. Tools registered as mcp__<server>__<tool>.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "ToolSearch".into(),
                description: "Search for and load deferred tool schemas on demand.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]
                }),
            },
            usage_hint: "ToolSearch(query) - loads deferred tool schemas on demand so the prompt only carries active tools.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "WorkflowPlan".into(),
                description: "Emit a dynamic orchestration plan that the workflow engine executes with a parallel subagent fleet.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {"plan": {"type": "object", "description": "WorkflowPlan JSON (phases, agents, edges)."}},
                    "required": ["plan"]
                }),
            },
            usage_hint: "WorkflowPlan(plan) - lets the model write an orchestration plan for a workflow engine to run with parallel subagents.".into(),
        },
        ToolEntry {
            definition: ToolDefinition {
                name: "WorktreeCreate".into(),
                description: "Create an isolated git worktree for the agent to work in.".into(),
                parameters: serde_json::json!({
                    "type": "object",
                    "properties": {"branch": {"type": "string"}}
                }),
            },
            usage_hint: "WorktreeCreate(branch) - creates an isolated git worktree for safe parallel development.".into(),
        },
    ]
}
