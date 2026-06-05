//! Core agent orchestration for Lamark.

use crate::harness::{HarnessStack, RealizationDecision, RegulationOutput, ToolExecutor};
use crate::prompt::SystemPrompt;
use crate::sanitize;
use crate::tool::{ToolDefinition, ToolResult};
use crate::turn::{Conversation, Message, TurnContext};
use crate::LLMClient;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, LazyLock};

/// Cache for context file reads (path → (mtime, content)).
static CONTEXT_FILE_CACHE: LazyLock<std::sync::RwLock<HashMap<PathBuf, (u64, String)>>> =
    LazyLock::new(std::sync::RwLock::default);

/// The main orchestrator for an AI agent turn.
pub struct AIAgent {
    /// The LLM client used for generation.
    pub client: Arc<dyn LLMClient>,
    /// The harness stack used for lifecycle regulation.
    pub harness: Arc<dyn HarnessStack>,
    /// The tool executor that handles actual tool calls.
    pub executor: Arc<dyn ToolExecutor>,
}

impl AIAgent {
    pub fn new(
        client: Arc<dyn LLMClient>,
        harness: Arc<dyn HarnessStack>,
        executor: Arc<dyn ToolExecutor>,
    ) -> Self {
        Self {
            client,
            harness,
            executor,
        }
    }

    /// Returns the list of core tool definitions from the registry.
    pub fn core_tools() -> Vec<ToolDefinition> {
        crate::tool::registry()
            .into_iter()
            .map(|entry| entry.definition)
            .collect()
    }

    /// Returns the usage hints for core tools.
    pub fn core_tool_hints() -> Vec<String> {
        crate::tool::registry()
            .into_iter()
            .map(|entry| entry.usage_hint)
            .collect()
    }

    /// Executes a single turn of the agent loop.
    pub async fn run_turn(
        &self,
        config: Arc<lamark_config::Config>,
        conversation: &mut Conversation,
        user_input: String,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        // 1. Add user input to conversation
        conversation.add_message(Message::User(user_input));

        // 2. Assemble system prompt (stable, context, volatile tiers)
        let system_prompt = self.assemble_system_prompt(config.clone(), conversation);

        // 3. First LLM call with tool definitions
        let tools = Self::core_tools();
        let mut response = self
            .client
            .generate(&system_prompt.render(), conversation, &tools)
            .await?;
        let mut tool_iter = 0usize;

        // 4. Handle tool calls if any (iteration limit from config)
        let max_iterations = config.max_tool_iterations.max(1);
        while !response.tool_calls.is_empty() && tool_iter < max_iterations {
            tracing::debug!(
                tool_count = response.tool_calls.len(),
                "LLM response with tool calls"
            );
            if let Some(content) = &response.content {
                conversation.add_message(Message::Assistant(content.clone()));
            }

            let mut tool_results = Vec::new();

            for call in &response.tool_calls {
                tracing::debug!(tool = %call.name, "Executing tool call");
                let ctx = TurnContext::new(conversation, config.clone());

                // Layer 3: Action Realization — validate then execute
                match self.harness.realize_action(call, &ctx) {
                    RealizationDecision::Exec => {
                        let result = self.executor.execute(call, &ctx).await;
                        tool_results.push(result);
                    }
                    crate::harness::RealizationDecision::Block { message } => {
                        tool_results.push(ToolResult::failure(message));
                    }
                }
            }

            // Append results to conversation + trajectory regulation
            for res in tool_results {
                conversation.add_message(Message::Tool(res.clone()));
                let ctx = TurnContext::new(conversation, config.clone());
                match self.harness.regulate_trajectory(&ctx, &res) {
                    RegulationOutput::None => {}
                    reg @ RegulationOutput::Hint { .. }
                    | reg @ RegulationOutput::Warning { .. }
                    | reg @ RegulationOutput::Directive { .. } => {
                        if let RegulationOutput::Hint { message } = &reg {
                            conversation.add_message(Message::System(format!("[HINT] {message}")));
                        } else if let RegulationOutput::Warning { message } = &reg {
                            conversation
                                .add_message(Message::System(format!("[WARNING] {message}")));
                        } else if let RegulationOutput::Directive { message } = &reg {
                            conversation
                                .add_message(Message::System(format!("[DIRECTIVE] {message}")));
                        }
                        tracing::warn!("Harness regulation: {:?}", reg);
                    }
                }
            }

            tool_iter += 1;
            response = self
                .client
                .generate(&system_prompt.render(), conversation, &tools)
                .await?;
        }

        Ok(response
            .content
            .unwrap_or_else(|| "No response from agent".to_string()))
    }

    fn assemble_system_prompt(
        &self,
        config: Arc<lamark_config::Config>,
        conversation: &Conversation,
    ) -> SystemPrompt {
        let stable = Self::build_stable_tier(config.clone(), &Self::core_tools());
        let context = Self::build_context_tier(&config);
        let volatile = Self::build_volatile_tier(config, conversation);

        SystemPrompt {
            stable,
            context,
            volatile,
        }
    }

    fn build_stable_tier(config: Arc<lamark_config::Config>, tools: &[ToolDefinition]) -> String {
        let mut parts: Vec<String> = Vec::new();

        // 1. Identity (primary) — sanitize for injected characters
        if !config.agent.identity.is_empty() {
            let identity = sanitize::sanitize_for_prompt(&config.agent.identity);
            parts.push(identity);
        }

        // 2. Mandatory tool-use directive
        parts.push(
            "MANDATORY: When any information requires lookup, data retrieval, file operations, \
             or command execution — CALL THE TOOL. Do NOT describe what you would do. \
             Do NOT say \"let me search\" or \"I will check\" without actually calling a tool. \
             Every answer that involves real-world data or code must use tools."
                .to_string(),
        );

        // 3. Task completion guidance
        if config.agent.task_completion_guidance.unwrap_or(true) {
            parts.push(
                "Always complete your work fully. Do not stop at a stub when real execution is possible."
                    .to_string(),
            );
        }

        // 4. Tool-use directive when tools are available
        if !tools.is_empty() {
            parts.push(
                "You have tools available. When you need information, data, or actions beyond your knowledge, CALL the appropriate tool directly — do not describe what you would do. Use the tool schema provided in the tools array."
                    .to_string(),
            );
        }

        // 5. Model-family operational guidance
        let model_lower = config.model.model_id.to_lowercase();
        if model_lower.contains("gemma") || model_lower.contains("gemini") {
            parts.push(
                "Use absolute paths for file operations. When editing files, verify the edit took effect."
                    .to_string(),
            );
        }

        // 6. Platform hints
        let platform = std::env::consts::OS;
        if platform == "macos" {
            parts.push(
                "You are running on macOS. Use macOS conventions for file paths and shell commands."
                    .to_string(),
            );
        }

        // 7. Harness layers
        let harness_text: String = Self::harness_layers(&config)
            .into_iter()
            .map(|(name, guidance)| format!("[{name}] {guidance}"))
            .collect::<Vec<_>>()
            .join("\n");

        if !harness_text.is_empty() {
            parts.push(harness_text);
        }

        parts.join("\n\n")
    }

    fn build_context_tier(config: &lamark_config::Config) -> String {
        let mut parts: Vec<String> = Vec::new();

        // Scan config.project_dir for context files (AGENTS.md, .cursorrules, etc.)
        let project_dir = &config.project_dir;
        if project_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(project_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    if matches!(name, "AGENTS.md" | ".cursorrules" | "SOUL.md") && path.is_file() {
                        // Check cache (path → (mtime, content))
                        let current_mtime = path
                            .metadata()
                            .ok()
                            .and_then(|m| m.modified().ok())
                            .and_then(|t| {
                                t.duration_since(std::time::UNIX_EPOCH)
                                    .ok()
                                    .map(|d| d.as_millis() as u64)
                            });

                        let content = if let Some((cached_mtime, cached_content)) =
                            CONTEXT_FILE_CACHE
                                .read()
                                .ok()
                                .and_then(|c| c.get(&path).cloned())
                        {
                            // Cache hit if mtime matches
                            if let Some(file_mtime) = current_mtime {
                                if cached_mtime == file_mtime {
                                    Some(cached_content)
                                } else {
                                    None // mtime changed, re-read
                                }
                            } else {
                                None
                            }
                        } else {
                            None // cache miss
                        };

                        let content = match content {
                            Some(c) => c,
                            None => match std::fs::read_to_string(&path) {
                                Ok(c) => {
                                    if let Some(file_mtime) = current_mtime {
                                        if let Ok(mut cache) = CONTEXT_FILE_CACHE.write() {
                                            cache.insert(path.clone(), (file_mtime, c.clone()));
                                        }
                                    }
                                    c
                                }
                                Err(_) => continue,
                            },
                        };

                        let trimmed = content.trim();
                        if !trimmed.is_empty() {
                            // Check for prompt injection
                            if let Some(injection_msg) = sanitize::check_injection(trimmed) {
                                parts.push(format!("## [BLOCKED: {}] {}", name, injection_msg));
                            } else {
                                let safe_content = sanitize::sanitize_for_prompt(trimmed);
                                parts.push(format!("## {}\n{}", name, safe_content.trim()));
                            }
                        }
                    }
                }
            }
        }

        parts.join("\n\n")
    }

    fn build_volatile_tier(
        config: Arc<lamark_config::Config>,
        conversation: &Conversation,
    ) -> String {
        let mut parts: Vec<String> = Vec::new();

        // Harness volatile injection (e.g., memory state)
        let harness_volatile = Self::harness_layers_volatile(&config, conversation);
        if !harness_volatile.is_empty() {
            parts.push(harness_volatile);
        }

        // Session info
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default();
        let days = now.as_secs() / 86400;
        let _year = 1970 + days / 365;

        parts.push(format!(
            "Model: {}\nProvider: {}\nSession: active",
            config.model.model_id, config.model.provider
        ));

        parts.join("\n\n")
    }

    fn harness_layers(config: &lamark_config::Config) -> Vec<(&'static str, String)> {
        let mut layers = Vec::new();

        // Layer 1: Environment detection
        let cwd = std::env::current_dir()
            .ok()
            .and_then(|p| p.to_str().map(|s| s.to_string()))
            .unwrap_or_else(|| "?".to_string());
        let os = std::env::consts::OS;
        let arch = std::env::consts::ARCH;
        layers.push((
            "Environment",
            format!("Platform: {os}/{arch}\nWorking directory: {cwd}"),
        ));

        // Layer 2: Config-derived layers
        if !config.plugins.is_empty() {
            let plugin_list = config.plugins.join(", ");
            layers.push(("Plugins", format!("Active plugins: {plugin_list}")));
        }

        layers
    }

    fn harness_layers_volatile(
        _config: &lamark_config::Config,
        conversation: &Conversation,
    ) -> String {
        let message_count = conversation.messages.len();
        if message_count > 0 {
            format!("Conversation state: {message_count} messages exchanged")
        } else {
            String::new()
        }
    }
}
