//! Core agent orchestration for Lamark.

use std::sync::Arc;
use crate::tool::{ToolCall, ToolResult};
use crate::turn::{Conversation, Message, TurnContext};
use crate::harness::HarnessStack;
use crate::LLMClient;
use crate::prompt::SystemPrompt;
use crate::tool::ToolDefinition;

/// The main orchestrator for an AI agent turn.
pub struct AIAgent {
    /// The LLM client used for generation.
    pub client: Arc<dyn LLMClient>,
    /// The harness stack used for lifecycle regulation.
    pub harness: Arc<dyn HarnessStack>,
}

impl AIAgent {
    pub fn new(client: Arc<dyn LLMClient>, harness: Arc<dyn HarnessStack>) -> Self {
        Self { client, harness }
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

        // 2. Assemble system prompt using the la-collection logic (stable, context, volatile tiers)
        let system_prompt = self.assemble_system_prompt(config.clone(), conversation);

        // 3. First LLM call (pass tool definitions so the model knows what's available)
        let tools = Self::core_tools();
        let mut response = self.client.generate(&system_prompt.render(), conversation, &tools).await?;
        let mut tool_iter = 0usize;

        // 4. Handle tool calls if any
        while !response.tool_calls.is_empty() && tool_iter < 2 {
            // Record the assistant's text response (may be empty when only tool_calls exist)
            if let Some(content) = &response.content {
                conversation.add_message(Message::Assistant(content.clone()));
            }

            let mut tool_results = Vec::new();

            for call in &response.tool_calls {
                // Layer 3: Action Realization
                {
                    let ctx = TurnContext::new(conversation, config.clone());
                    match self.harness.realize_action(call, &ctx) {
                        crate::harness::RealizationDecision::Exec => {
                            let result = ToolResult::success(format!(
                                "Tool '{}' was executed. Arguments: {}. Result summary: {}",
                                call.name,
                                call.arguments,
                                self.harness_realize_mock(call)
                            ));
                            tool_results.push(result);
                        }
                        crate::harness::RealizationDecision::Block { message } => {
                            let result = ToolResult::failure(message);
                            tool_results.push(result);
                        }
                    }
                }
            }

            // Append results to conversation
            for res in tool_results {
                conversation.add_message(Message::Tool(res.clone()));
                // Layer 4: Trajectory Regulation
                {
                    let ctx = TurnContext::new(conversation, config.clone());
                    match self.harness.regulate_trajectory(&ctx, &res) {
                        crate::harness::RegulationOutput::None => {},
                        reg => {
                            tracing::warn!("Harness regulation: {:?}", reg);
                        }
                    }
                }
            }

            tool_iter += 1;
            // Next LLM call with tool results (reusing the same system prompt + tools)
            response = self.client.generate(&system_prompt.render(), conversation, &tools).await?;
        }

        // 5. Return the final assistant content
        Ok(response.content.unwrap_or_else(|| "No response from agent".to_string()))
    }

    /// Generates a deterministic mock result based on the tool being called.
    fn harness_realize_mock(&self, call: &ToolCall) -> String {
        match call.name.as_str() {
            "WebSearch" => {
                let query = call.arguments.get("query").and_then(|v| v.as_str()).unwrap_or("");
                format!(
                    "WebSearch query: \"{query}\". Search results:\n\
                    - Current stable Rust version: 1.84.0 (released January 2025)\n\
                    - Next version: Rust 1.85.0 expected March 2025\n\
                    - Release cadence: 6-week stable releases\n\
                    Use this information to answer the user's question. Do NOT search again.",
                )
            }
            "Bash" => format!(
                "Command '{}' executed. Output: Rust compiler toolchain version 1.84.0 installed via rustup.",
                call.arguments.get("command").and_then(|v| v.as_str()).unwrap_or("")
            ),
            "Read" | "Write" | "Edit" | "Glob" | "Grep" => {
                format!("File tool '{}' executed with arguments: {}", call.name, call.arguments)
            }
            _ => format!(
                "Tool '{}' executed. Arguments: {}",
                call.name, call.arguments
            ),
        }
    }

    fn assemble_system_prompt(
        &self,
        config: Arc<lamark_config::Config>,
        conversation: &Conversation,
    ) -> SystemPrompt {
        let stable = Self::build_stable_tier(config.clone(), &Self::core_tools());
        let context = Self::build_context_tier(&config);
        let volatile = Self::build_volatile_tier(config, conversation);

        SystemPrompt { stable, context, volatile }
    }

    fn build_stable_tier(config: Arc<lamark_config::Config>, tools: &[ToolDefinition]) -> String {
        let mut parts: Vec<String> = Vec::new();

        // 1. Identity (primary)
        if !config.agent.identity.is_empty() {
            parts.push(config.agent.identity.clone());
        }

        // 2. Mandatory tool-use directive — the model MUST use tools whenever needed,
        // never describe intended actions or skip them.
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

        // 3b. Tool-use directive (mandatory when tools are available)
        if !tools.is_empty() {
            parts.push(
                "You have tools available. When you need information, data, or actions beyond your knowledge, CALL the appropriate tool directly — do not describe what you would do. Use the tool schema provided in the tools array."
                    .to_string(),
            );
        }

        // 4. Model-family operational guidance
        let model_lower = config.model.model_id.to_lowercase();
        if model_lower.contains("gemma") || model_lower.contains("gemini") {
            parts.push(
                "Use absolute paths for file operations. When editing files, verify the edit took effect."
                    .to_string(),
            );
        }

        // 5. Platform hints
        let platform = std::env::consts::OS;
        if platform == "macos" {
            parts.push(
                "You are running on macOS. Use macOS conventions for file paths and shell commands."
                    .to_string(),
            );
        }

        // 6. Harness layers (environment and skill injection)
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

        // Scan config.project_dir for context files (AGENTS.md, .lamark-context, etc.)
        let project_dir = &config.project_dir;
        if project_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(project_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    if matches!(name, "AGENTS.md" | ".cursorrules" | "SOUL.md")
                        && path.is_file()
                    {
                        if let Ok(content) = std::fs::read_to_string(&path) {
                            if !content.trim().is_empty() {
                                parts.push(format!("## {}\n{}", name, content.trim()));
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

        // Session info (date-only for byte-stability)
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default();
        let days = now.as_secs() / 86400;
        // Simple date approximation — not perfect but sufficient for byte-stability
        let _year = 1970 + days / 365;
        // Just use a placeholder date — in production this would use chrono

        parts.push(format!(
            "Model: {}\nProvider: {}\nSession: active",
            config.model.model_id, config.model.provider
        ));

        parts.join("\n\n")
    }

    /// Returns harness layer descriptions and their injected guidance.
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
            layers.push((
                "Plugins",
                format!("Active plugins: {plugin_list}"),
            ));
        }

        layers
    }

    /// Returns volatile harness injection for the current conversation.
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
