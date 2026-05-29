//! The `Tool` trait and associated types for the Lamark tool registry.
//!
//! See `docs/plan/05-layer-4-agent-core.md`.

use lamark_core::tool::{ToolCall, ToolResult};
use lamark_core::turn::TurnContext;

/// Capabilities advertised by a tool.
#[derive(Debug, Clone, Default)]
pub struct ToolCapabilities {
    pub is_read_only: bool,
    pub is_destructive: bool,
    pub is_concurrency_safe: bool,
    pub max_result_size_chars: Option<usize>,
}

/// JSON Schema for tool parameters (raw `serde_json::Value`).
pub type ToolSchema = serde_json::Value;

/// Every built-in and plugin tool implements this trait.
///
/// Implementations live in `lamark-tools`; registered at startup by the binary.
/// `invoke` is object-safe via boxed `Future` return so the registry can hold
/// `Arc<dyn Tool>`.
pub trait Tool: Send + Sync {
    /// Stable snake_case tool name used in function-calling API.
    fn name(&self) -> &str;
    /// JSON Schema for the input parameters.
    fn schema(&self) -> ToolSchema;
    /// Capability hints for concurrency, sandboxing, and approval.
    fn capabilities(&self) -> ToolCapabilities {
        ToolCapabilities::default()
    }
    /// Execute the tool, returning a result to be forwarded to the model.
    fn invoke<'a>(
        &'a self,
        call: &'a ToolCall,
        ctx: &'a TurnContext<'_>,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = lamark_core::Result<ToolResult>> + Send + 'a>,
    >;
}
