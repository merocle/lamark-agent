//! Turn and context definitions for Lamark core.

use crate::tool::ToolResult;
use std::sync::Arc;

/// A single message in a conversation.
#[derive(Debug, Clone)]
pub enum Message {
    /// User input.
    User(String),
    /// Assistant response.
    Assistant(String),
    /// Result of a tool call.
    Tool(ToolResult),
    /// System instruction.
    System(String),
}

/// A collection of messages representing a conversation.
#[derive(Debug, Clone, Default)]
pub struct Conversation {
    pub messages: Vec<Message>,
}

impl Conversation {
    pub fn add_message(&mut self, msg: Message) {
        self.messages.push(msg);
    }

    pub fn last_message(&self) -> Option<&Message> {
        self.messages.last()
    }
}

/// Context passed to harness layers and the agent loop for a single turn.
pub struct TurnContext<'a> {
    /// The conversation history so far.
    pub conversation: &'a Conversation,
    /// The active configuration.
    pub config: Arc<lamark_config::Config>,
}

impl<'a> TurnContext<'a> {
    pub fn new(conversation: &'a Conversation, config: Arc<lamark_config::Config>) -> Self {
        Self {
            conversation,
            config,
        }
    }
}
