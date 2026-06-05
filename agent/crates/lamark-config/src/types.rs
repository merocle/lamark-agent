//! Configuration types for Lamark.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Resolved configuration after merging all layers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Active profile name (e.g. "dev", "prod").
    pub profile: String,

    /// Model provider configuration.
    pub model: ModelConfig,

    /// Agent identity / personality settings.
    pub agent: AgentConfig,

    /// Enabled plugins (by name).
    pub plugins: Vec<String>,

    /// Paths to scan for skills.
    pub skill_paths: Vec<PathBuf>,

    /// Project root (where .lamark/ lives).
    pub project_dir: PathBuf,

    /// Knowledge-base URL for memory/web tools (optional).
    pub knowledge_base_url: Option<String>,

    /// Maximum tool call iterations per turn.
    #[serde(default = "default_max_tool_iterations")]
    pub max_tool_iterations: usize,
}

fn default_max_tool_iterations() -> usize {
    5
}

impl Default for Config {
    fn default() -> Self {
        Self {
            profile: "default".to_string(),
            model: ModelConfig::default(),
            agent: AgentConfig::default(),
            plugins: Vec::new(),
            skill_paths: vec![PathBuf::from("~/.lamark/skills")],
            project_dir: PathBuf::from("."),
            knowledge_base_url: None,
            max_tool_iterations: 5,
        }
    }
}

/// Model provider configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelConfig {
    /// Provider name (anthropic, openai, ollama, etc.).
    pub provider: String,

    /// Model identifier (e.g. "claude-3-5-sonnet-20240620").
    pub model_id: String,

    /// API key (from environment or secrets store).
    pub api_key: Option<String>,

    /// Base URL for custom endpoints.
    pub base_url: Option<String>,

    /// Reasoning effort (none, low, medium, high, xhigh).
    pub reasoning: Option<String>,
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            provider: "openai".to_string(),
            model_id: "gpt-4o".to_string(),
            api_key: None,
            base_url: None,
            reasoning: None,
        }
    }
}

impl ModelConfig {
    /// Returns the effective base URL, falling back to a provider-specific default.
    pub fn base_url(&self) -> String {
        self.base_url
            .clone()
            .unwrap_or_else(|| match self.provider.as_str() {
                "openai" => "http://127.0.0.1:52415/v1".to_string(),
                _ => "http://localhost:8080".to_string(),
            })
    }

    /// Returns the effective API key, falling back to a provider-specific default.
    pub fn api_key(&self) -> String {
        self.api_key
            .clone()
            .unwrap_or_else(|| match self.provider.as_str() {
                "openai" => "no-api-key-needed".to_string(),
                _ => "".to_string(),
            })
    }
}

/// Agent identity and behavior settings.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConfig {
    /// SOUL.md identity content.
    pub identity: String,

    /// Whether to require approval for destructive commands.
    pub safety: SafetyMode,

    /// Conversation context window (in tokens).
    pub max_tokens: Option<usize>,

    /// Whether to inject task completion guidance.
    pub task_completion_guidance: Option<bool>,

    /// Tool-use enforcement configuration.
    pub tool_use_enforcement: Option<ToolUseEnforcement>,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            identity: String::new(),
            safety: SafetyMode::default(),
            max_tokens: None,
            task_completion_guidance: Some(true),
            tool_use_enforcement: None,
        }
    }
}

/// Tool-use enforcement configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ToolUseEnforcement {
    Auto,
    Always,
    Never,
    Custom(Vec<String>),
}

impl Default for ToolUseEnforcement {
    fn default() -> Self {
        ToolUseEnforcement::Auto
    }
}

/// Safety mode for command execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SafetyMode {
    AskFirst,   // Prompt before every command
    Yolo,       // Skip all approvals
    Permissive, // Only block filesystem/terminal writes
}

impl Default for SafetyMode {
    fn default() -> Self {
        SafetyMode::AskFirst
    }
}

/// Options controlling how [`load`] / [`load_or_init`] resolves configuration.
#[derive(Debug, Clone, Default)]
pub struct LoadOptions {
    /// Override config file path.
    pub config_file: Option<PathBuf>,

    /// Profile name to use from the config file.
    pub profile: Option<String>,

    /// Dot-notation overrides applied after profile merging (e.g. "model.provider=ollama").
    pub overrides: Vec<String>,
}

impl LoadOptions {
    /// Resolve the absolute path to a config file, or return None for auto-discovery.
    pub fn resolve_config_path(&self) -> Option<PathBuf> {
        self.config_file.clone()
    }
}
