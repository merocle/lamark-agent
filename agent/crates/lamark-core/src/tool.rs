//! Core tool-call and tool-result primitives.

use serde::{Deserialize, Serialize};

/// An opaque identifier for a single tool call within a turn.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ToolCallId(String);

impl ToolCallId {
    /// Create a new call ID (typically `call_<uuid>`).
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }

    /// Return the raw string value.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for ToolCallId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

/// A function call issued by the model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionCall {
    /// The name of the tool to invoke.
    pub name: String,
    /// Raw JSON-encoded arguments string (as returned by the model).
    pub arguments: String,
}

/// A structured tool call emitted by the model in a single turn.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    /// Unique ID for this call within its turn.
    pub id: ToolCallId,
    /// Always `"function"` in the OpenAI protocol.
    #[serde(rename = "type")]
    pub call_type: String,
    /// The function being invoked.
    pub function: FunctionCall,
}

impl ToolCall {
    /// Convenience constructor.
    pub fn function(
        id: impl Into<String>,
        name: impl Into<String>,
        arguments: impl Into<String>,
    ) -> Self {
        Self {
            id: ToolCallId::new(id),
            call_type: "function".into(),
            function: FunctionCall {
                name: name.into(),
                arguments: arguments.into(),
            },
        }
    }

    /// Decode the arguments as a typed value.
    ///
    /// Returns an error if the raw argument string is not valid JSON or does
    /// not match the expected schema.
    pub fn decode_args<T: serde::de::DeserializeOwned>(&self) -> crate::Result<T> {
        serde_json::from_str(&self.function.arguments).map_err(|e| {
            crate::Error::Other(format!(
                "failed to decode args for {}: {e}",
                self.function.name
            ))
        })
    }
}

/// The result of executing a tool call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    /// ID of the originating call.
    pub id: ToolCallId,
    /// Text content returned to the model.
    pub content: String,
    /// Whether this result represents an execution error.
    pub is_error: bool,
}

impl ToolResult {
    /// Create a successful tool result.
    pub fn ok(id: ToolCallId, content: impl Into<String>) -> Self {
        Self {
            id,
            content: content.into(),
            is_error: false,
        }
    }

    /// Create an error tool result.
    pub fn err(id: ToolCallId, message: impl Into<String>) -> Self {
        Self {
            id,
            content: message.into(),
            is_error: true,
        }
    }
}
