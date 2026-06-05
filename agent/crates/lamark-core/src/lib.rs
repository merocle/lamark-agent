//! Lamark core — fundamental agent abstractions.

pub mod tool;
pub mod turn;
pub mod harness;
pub mod agent;
pub mod prompt;

pub use tool::{ToolCall, ToolDefinition, ToolResult};
pub use turn::{Conversation, Message, TurnContext};
pub use agent::AIAgent;
pub use harness::{HarnessStack, PassthroughHarness};
pub use prompt::SystemPrompt;

/// A trait for interacting with various LLM providers.
#[async_trait::async_trait]
pub trait LLMClient: Send + Sync {
    /// Generate a response based on the conversation, system prompt, and tool definitions.
    async fn generate(
        &self,
        system_prompt: &str,
        conversation: &Conversation,
        tools: &[ToolDefinition],
    ) -> Result<LLMResponse, Box<dyn std::error::Error + Send + Sync>>;
}

/// The response from an LLM, which may contain text and tool calls.
#[derive(Debug, Clone)]
pub struct LLMResponse {
    /// The textual response from the model.
    pub content: Option<String>,
    /// Tool calls requested by the model.
    pub tool_calls: Vec<ToolCall>,
}
