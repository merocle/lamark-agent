//! The `HarnessStack` trait — runtime interface between the turn loop and the
//! four LIFE-HARNESS lifecycle layers.
//!
//! `lamark-core` defines the trait; `lamark-harness` provides the canonical
//! implementation. The agent binary (`lamark`) wires them together at startup.
//!
//! See `docs/plan/10d-skillopt-life-harness.md` for the full specification.

use crate::tool::{ToolCall, ToolResult};
use crate::turn::TurnContext;

/// Decision returned by the Action Realization layer.
///
/// Distinct from `lamark-policy` decisions (which concern user authorisation).
/// `RealizationDecision` concerns technical correctness of the tool call.
#[derive(Debug, Clone)]
pub enum RealizationDecision {
    /// The call is valid; pass it to the sandbox for execution.
    Exec,
    /// The call violates a realization rule; block it and return `message` to
    /// the model so it can self-correct within the same turn.
    Block {
        /// Model-visible error message (no user prompt is shown).
        message: String,
    },
}

/// Output produced by the Trajectory Regulation layer.
#[derive(Debug, Clone, Default)]
pub enum RegulationOutput {
    /// No intervention needed.
    #[default]
    None,
    /// Soft hint — agent may ignore.
    Hint { message: String },
    /// Explicit warning with a retry suggestion.
    Warning { message: String },
    /// Hard directive — the model must acknowledge this before continuing.
    Directive { message: String },
}

/// The harness stack wraps every agent turn with four lifecycle layers:
///
/// 1. **Environment Contract** — enriches the system prompt with evolved ΔC.
/// 2. **Procedural Skill** — retrieves and injects relevant skill documents.
/// 3. **Action Realization** — validates each tool call before sandbox execution.
/// 4. **Trajectory Regulation** — detects and interrupts degenerate episodes.
///
/// Implemented by `lamark-harness`; injected into `AIAgent` at startup via
/// `Arc<dyn HarnessStack>`.
pub trait HarnessStack: Send + Sync {
    /// Called once per turn, before the first LLM inference call.
    ///
    /// Implementations should mutate `system_prompt` in-place to inject
    /// the contract delta (layer 1) and retrieved skill content (layer 2).
    fn prepare_context(&self, ctx: &TurnContext<'_>, system_prompt: &mut String);

    /// Called after the model emits a tool call, before sandbox execution.
    ///
    /// Returns `Exec` to proceed or `Block { message }` to return an
    /// error to the model. Must not spawn tasks or block for long — it
    /// runs in the hot path of the turn loop.
    fn realize_action(&self, call: &ToolCall, ctx: &TurnContext<'_>) -> RealizationDecision;

    /// Called after each tool result is appended to the conversation.
    ///
    /// Returns `None` for most steps. Returns `Hint / Warning / Directive`
    /// when degeneration is detected. The caller is responsible for
    /// inserting the message into the conversation before the next inference.
    fn regulate_trajectory(
        &self,
        ctx: &TurnContext<'_>,
        last_result: &ToolResult,
    ) -> RegulationOutput;
}

/// A no-op harness stack; useful for tests and minimal deployments where
/// harness evolution has not yet been run.
#[derive(Debug, Default)]
pub struct PassthroughHarness;

impl HarnessStack for PassthroughHarness {
    fn prepare_context(&self, _ctx: &TurnContext<'_>, _system_prompt: &mut String) {}

    fn realize_action(&self, _call: &ToolCall, _ctx: &TurnContext<'_>) -> RealizationDecision {
        RealizationDecision::Exec
    }

    fn regulate_trajectory(
        &self,
        _ctx: &TurnContext<'_>,
        _last_result: &ToolResult,
    ) -> RegulationOutput {
        RegulationOutput::None
    }
}
