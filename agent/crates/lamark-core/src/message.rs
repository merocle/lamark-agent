//! Role-tagged message types shared across the Lamark runtime.

use crate::tool::{ToolCall, ToolCallId};
use serde::{Deserialize, Serialize};

/// Role of a message in a conversation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// The system / developer message.
    System,
    /// A message from the human user.
    User,
    /// A message produced by the model.
    Assistant,
    /// A tool result message.
    Tool,
}

/// A single message in a conversation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    /// Role of the sender.
    pub role: Role,
    /// Text content (may be empty when tool_calls is non-empty).
    pub content: String,
    /// Optional reasoning / thinking content (used by reasoning models).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning_content: Option<String>,
    /// Tool calls emitted by the assistant in this turn.
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub tool_calls: Vec<ToolCall>,
    /// For Role::Tool messages: the ID of the call this result belongs to.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<ToolCallId>,
}

impl Message {
    /// Construct a system message.
    pub fn system(content: impl Into<String>) -> Self {
        Self {
            role: Role::System,
            content: content.into(),
            reasoning_content: None,
            tool_calls: vec![],
            tool_call_id: None,
        }
    }

    /// Construct a user message.
    pub fn user(content: impl Into<String>) -> Self {
        Self {
            role: Role::User,
            content: content.into(),
            reasoning_content: None,
            tool_calls: vec![],
            tool_call_id: None,
        }
    }

    /// Construct an assistant message (no tool calls).
    pub fn assistant(content: impl Into<String>) -> Self {
        Self {
            role: Role::Assistant,
            content: content.into(),
            reasoning_content: None,
            tool_calls: vec![],
            tool_call_id: None,
        }
    }

    /// Construct a tool result message.
    pub fn tool_result(id: ToolCallId, content: impl Into<String>) -> Self {
        Self {
            role: Role::Tool,
            content: content.into(),
            reasoning_content: None,
            tool_calls: vec![],
            tool_call_id: Some(id),
        }
    }
}
