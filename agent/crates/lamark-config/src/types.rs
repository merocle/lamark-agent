//! Configuration type hierarchy.
//!
//! Each block maps directly to the matching YAML section in
//! `~/.lamark/config.yaml`. See `plan/03-layer-2-config-bootstrap.md` for the
//! full reference.
#![allow(missing_docs)] // field-level docs are in the plan; added as the API stabilises

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

// ─── Root ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct Config {
    pub project_id:    String,
    pub agent_id:      Option<String>,
    pub model:         ModelConfig,
    pub providers:     HashMap<String, ProviderTuning>,
    pub agent:         AgentConfig,
    pub sandbox:       SandboxConfig,
    pub memory:        MemoryConfig,
    pub skills:        SkillsConfig,
    pub plugins:       PluginsConfig,
    pub gateway:       GatewayConfig,
    pub mcp:           McpConfig,
    pub acp:           AcpConfig,
    pub trace:         TraceConfig,
    pub policy:        PolicyConfig,
    pub learning:      LearningConfig,
    pub observability: ObservabilityConfig,
    pub runtime:       RuntimeConfig,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            project_id:    "lamark-default".to_owned(),
            agent_id:      None,
            model:         ModelConfig::default(),
            providers:     HashMap::new(),
            agent:         AgentConfig::default(),
            sandbox:       SandboxConfig::default(),
            memory:        MemoryConfig::default(),
            skills:        SkillsConfig::default(),
            plugins:       PluginsConfig::default(),
            gateway:       GatewayConfig::default(),
            mcp:           McpConfig::default(),
            acp:           AcpConfig::default(),
            trace:         TraceConfig::default(),
            policy:        PolicyConfig::default(),
            learning:      LearningConfig::default(),
            observability: ObservabilityConfig::default(),
            runtime:       RuntimeConfig::default(),
        }
    }
}

// ─── Model ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct ModelConfig {
    pub provider:        String,
    pub base_url:        String,
    pub name:            String,
    pub api_key_env:     String,
    pub context_length:  u32,
    pub max_tokens:      u32,
    pub timeout_seconds: u64,
    pub reasoning:       ReasoningConfig,
    pub tool_call:       ToolCallConfig,
    pub cache:           CacheConfig,
    pub fallback_chain:  Vec<String>,
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            provider:        "vllm".to_owned(),
            base_url:        "http://localhost:8000/v1".to_owned(),
            name:            "Qwen/Qwen3-8B-Instruct".to_owned(),
            api_key_env:     "LAMARK_MODEL_KEY".to_owned(),
            context_length:  32768,
            max_tokens:      8192,
            timeout_seconds: 600,
            reasoning:       ReasoningConfig::default(),
            tool_call:       ToolCallConfig::default(),
            cache:           CacheConfig::default(),
            fallback_chain:  Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct ReasoningConfig {
    pub parser:                String,
    pub inject_think_in_trace: bool,
}

impl Default for ReasoningConfig {
    fn default() -> Self {
        Self {
            parser:                "none".to_owned(),
            inject_think_in_trace: true,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct ToolCallConfig {
    pub parser:               String,
    pub parallel_tool_calls:  bool,
}

impl Default for ToolCallConfig {
    fn default() -> Self {
        Self {
            parser:              "openai_json".to_owned(),
            parallel_tool_calls: true,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct CacheConfig {
    pub strategy:           String,
    pub ttl:                String,
    pub min_segment_tokens: u32,
}

impl Default for CacheConfig {
    fn default() -> Self {
        Self {
            strategy:           "auto".to_owned(),
            ttl:                "1h".to_owned(),
            min_segment_tokens: 1024,
        }
    }
}

// ─── Provider tuning ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct ProviderTuning {
    pub base_url:               Option<String>,
    pub request_timeout_seconds: Option<u64>,
    pub stale_timeout_seconds:  Option<u64>,
    pub cache_control_ttl:      Option<String>,
    pub api_key_env:            Option<String>,
}

// ─── Agent loop ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct AgentConfig {
    pub max_iterations:         u32,
    pub max_parallel_tool_calls: u32,
    pub worktree:               bool,
    pub tool_use_enforcement:   bool,
    pub default_policy:         String,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            max_iterations:          40,
            max_parallel_tool_calls: 8,
            worktree:                true,
            tool_use_enforcement:    true,
            default_policy:          "prompt".to_owned(),
        }
    }
}

// ─── Sandbox ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct SandboxConfig {
    pub default:       String,
    pub local:         LocalSandboxConfig,
    pub docker:        DockerSandboxConfig,
    pub ssh:           SshSandboxConfig,
    pub agent_hosting: AgentHostingConfig,
    pub timeout:       u64,
    pub lifetime_seconds: u64,
}

impl Default for SandboxConfig {
    fn default() -> Self {
        Self {
            default:          "docker".to_owned(),
            local:            LocalSandboxConfig::default(),
            docker:           DockerSandboxConfig::default(),
            ssh:              SshSandboxConfig::default(),
            agent_hosting:    AgentHostingConfig::default(),
            timeout:          180,
            lifetime_seconds: 600,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct LocalSandboxConfig {
    pub mode:                 String,
    pub unsafe_allow_host_fs: bool,
}

impl Default for LocalSandboxConfig {
    fn default() -> Self {
        Self {
            mode:                 "in-process".to_owned(),
            unsafe_allow_host_fs: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct DockerSandboxConfig {
    pub image:            String,
    pub egress:           String,
    pub egress_allowlist: Vec<String>,
    pub workspace_mount:  String,
    pub user:             String,
    pub cap_drop:         Vec<String>,
    pub memory_limit:     String,
    pub cpu_limit:        f32,
}

impl Default for DockerSandboxConfig {
    fn default() -> Self {
        Self {
            image:            "lamark/runner:latest".to_owned(),
            egress:           "model-provider-only".to_owned(),
            egress_allowlist: Vec::new(),
            workspace_mount:  "copy-on-write".to_owned(),
            user:             "1000:1000".to_owned(),
            cap_drop:         vec!["ALL".to_owned()],
            memory_limit:     "4g".to_owned(),
            cpu_limit:        2.0,
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct SshSandboxConfig {
    pub host:     Option<String>,
    pub user:     Option<String>,
    pub key_path: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct AgentHostingConfig {
    pub subagent_default:         String,
    pub trusted_role_default:     String,
    pub interrupt_grace_seconds:  u64,
}

impl Default for AgentHostingConfig {
    fn default() -> Self {
        Self {
            subagent_default:        "docker".to_owned(),
            trusted_role_default:    "local".to_owned(),
            interrupt_grace_seconds: 5,
        }
    }
}

// ─── Memory ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct MemoryConfig {
    pub external_provider: String,
    pub required:          bool,
    pub knowledge_base:    KbMemoryConfig,
    pub honcho:            HonchoConfig,
    pub mem0:              Mem0Config,
    pub fallback:          MemoryFallbackConfig,
}

impl Default for MemoryConfig {
    fn default() -> Self {
        Self {
            external_provider: "knowledge-base".to_owned(),
            required:          false,
            knowledge_base:    KbMemoryConfig::default(),
            honcho:            HonchoConfig::default(),
            mem0:              Mem0Config::default(),
            fallback:          MemoryFallbackConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct KbMemoryConfig {
    pub base_url:      String,
    pub project_id:    String,
    pub auth_token_env: String,
    pub timeout_seconds: u64,
    pub retry:         RetryConfig,
}

impl Default for KbMemoryConfig {
    fn default() -> Self {
        Self {
            base_url:       "http://localhost:8080".to_owned(),
            project_id:     "lamark-default".to_owned(),
            auth_token_env: "KB_TOKEN".to_owned(),
            timeout_seconds: 5,
            retry:          RetryConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct RetryConfig {
    pub max_attempts: u32,
    pub backoff_ms:   u64,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self { max_attempts: 3, backoff_ms: 200 }
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct HonchoConfig {
    pub base_url:    Option<String>,
    pub api_key_env: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct Mem0Config {
    pub api_key_env: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct MemoryFallbackConfig {
    pub sqlite_path: String,
}

impl Default for MemoryFallbackConfig {
    fn default() -> Self {
        Self { sqlite_path: "~/.lamark/memory.sqlite".to_owned() }
    }
}

// ─── Skills ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct SkillsConfig {
    pub search_paths:    Vec<String>,
    pub bundled_enabled: bool,
    pub curator:         CuratorConfig,
}

impl Default for SkillsConfig {
    fn default() -> Self {
        Self {
            search_paths:    vec![
                "./.lamark/skills".to_owned(),
                "~/.lamark/skills".to_owned(),
            ],
            bundled_enabled: true,
            curator:         CuratorConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct CuratorConfig {
    pub enable:                bool,
    pub interval_hours:        u32,
    pub auto_archive_after_days: u32,
    pub never_touch:           Vec<String>,
}

impl Default for CuratorConfig {
    fn default() -> Self {
        Self {
            enable:                  true,
            interval_hours:          168,
            auto_archive_after_days: 30,
            never_touch:             vec!["pinned".to_owned(), "user_authored".to_owned()],
        }
    }
}

// ─── Plugins ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct PluginsConfig {
    pub enable:              bool,
    pub search_paths:        Vec<String>,
    pub allow_dylib:         bool,
    pub allow_wasm:          bool,
    pub capability_grants:   CapabilityGrants,
}

impl Default for PluginsConfig {
    fn default() -> Self {
        Self {
            enable:            true,
            search_paths:      vec!["~/.lamark/plugins".to_owned()],
            allow_dylib:       false,
            allow_wasm:        true,
            capability_grants: CapabilityGrants::default(),
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct CapabilityGrants {
    pub fs_read:  Vec<String>,
    pub fs_write: Vec<String>,
    pub network:  Vec<String>,
    pub exec:     Vec<String>,
}

// ─── Gateway ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct GatewayConfig {
    pub enable:   bool,
    pub adapters: Vec<String>,
    pub bind:     String,
    pub telegram: TelegramConfig,
    pub slack:    SlackConfig,
    pub discord:  DiscordConfig,
}

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            enable:   false,
            adapters: Vec::new(),
            bind:     "127.0.0.1:5050".to_owned(),
            telegram: TelegramConfig::default(),
            slack:    SlackConfig::default(),
            discord:  DiscordConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct TelegramConfig {
    pub bot_token_env:   Option<String>,
    pub allow_user_ids: Vec<u64>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct SlackConfig {
    pub bot_token_env: Option<String>,
    pub app_token_env: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct DiscordConfig {
    pub bot_token_env: Option<String>,
}

// ─── MCP ─────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct McpConfig {
    pub servers: Vec<serde_json::Value>,
    pub server:  McpServerConfig,
}

impl Default for McpConfig {
    fn default() -> Self {
        Self {
            servers: Vec::new(),
            server:  McpServerConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct McpServerConfig {
    pub enable:       bool,
    pub transport:    String,
    pub bind:         String,
    pub expose_tools: Vec<String>,
}

impl Default for McpServerConfig {
    fn default() -> Self {
        Self {
            enable:       false,
            transport:    "stdio".to_owned(),
            bind:         "127.0.0.1:5051".to_owned(),
            expose_tools: vec![
                "read".to_owned(), "write".to_owned(), "edit".to_owned(),
                "bash".to_owned(), "grep".to_owned(), "memory_search".to_owned(),
            ],
        }
    }
}

// ─── ACP ─────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct AcpConfig {
    pub registry_url: Option<String>,
    pub identity:     AcpIdentityConfig,
}

impl Default for AcpConfig {
    fn default() -> Self {
        Self {
            registry_url: None,
            identity:     AcpIdentityConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct AcpIdentityConfig {
    pub name:     String,
    pub key_path: String,
}

impl Default for AcpIdentityConfig {
    fn default() -> Self {
        Self {
            name:     "lamark".to_owned(),
            key_path: "~/.lamark/acp/identity.key".to_owned(),
        }
    }
}

// ─── Trace ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct TraceConfig {
    pub enable:                    bool,
    pub root:                      String,
    pub capture_inference_payloads: bool,
    pub capture_tool_payloads:     bool,
    pub redact_inline:             bool,
    pub upload_to_kb:              bool,
    pub rotate:                    TraceRotateConfig,
}

impl Default for TraceConfig {
    fn default() -> Self {
        Self {
            enable:                     true,
            root:                       "~/.lamark/traces".to_owned(),
            capture_inference_payloads: true,
            capture_tool_payloads:      true,
            redact_inline:              false,
            upload_to_kb:               true,
            rotate:                     TraceRotateConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct TraceRotateConfig {
    pub keep_days: u32,
    pub max_gb:    u32,
}

impl Default for TraceRotateConfig {
    fn default() -> Self {
        Self { keep_days: 30, max_gb: 50 }
    }
}

// ─── Policy ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct PolicyConfig {
    pub file:    String,
    pub default: String,
}

impl Default for PolicyConfig {
    fn default() -> Self {
        Self {
            file:    "~/.lamark/policy.toml".to_owned(),
            default: "prompt".to_owned(),
        }
    }
}

// ─── Learning ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct LearningConfig {
    pub enable_collection: bool,
    pub consent_required:  bool,
}

impl Default for LearningConfig {
    fn default() -> Self {
        Self { enable_collection: true, consent_required: false }
    }
}

// ─── Observability ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct ObservabilityConfig {
    pub metrics:  MetricsConfig,
    pub langfuse: LangfuseConfig,
    pub wandb:    WandbConfig,
}

impl Default for ObservabilityConfig {
    fn default() -> Self {
        Self {
            metrics:  MetricsConfig::default(),
            langfuse: LangfuseConfig::default(),
            wandb:    WandbConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct MetricsConfig {
    pub enable: bool,
    pub port:   u16,
}

impl Default for MetricsConfig {
    fn default() -> Self {
        Self { enable: true, port: 9090 }
    }
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct LangfuseConfig {
    pub enable:         bool,
    pub base_url:       Option<String>,
    pub public_key_env: Option<String>,
    pub secret_key_env: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct WandbConfig {
    pub enable:  bool,
    pub project: String,
}

impl Default for WandbConfig {
    fn default() -> Self {
        Self { enable: false, project: "lamark".to_owned() }
    }
}

// ─── Runtime ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct RuntimeConfig {
    pub worker_threads:          Option<u32>,
    pub blocking_threads:        u32,
    pub shutdown_grace_seconds:  u64,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            worker_threads:         None,
            blocking_threads:       32,
            shutdown_grace_seconds: 10,
        }
    }
}
