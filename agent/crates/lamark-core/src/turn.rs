//! Turn context passed through the harness stack during every agent step.

use crate::ids::{SessionId, TurnId};
use crate::message::Message;

/// Snapshot of the conversation state at the start of a harness step.
///
/// Passed by shared reference to every `HarnessStack` method so the layers
/// can inspect history, remaining budget, and active tool context without
/// owning any of it.
#[derive(Debug)]
pub struct TurnContext<'a> {
    /// Stable session identifier.
    pub session_id: &'a SessionId,
    /// Current turn identifier.
    pub turn_id: &'a TurnId,
    /// Conversation history so far (including the current turn's messages).
    pub history: &'a [Message],
    /// Steps (LLM inference + tool calls) remaining before the session is
    /// force-terminated.
    pub budget_remaining: usize,
    /// Total step budget for this session.
    pub budget_total: usize,
}

impl<'a> TurnContext<'a> {
    /// Fraction of budget consumed so far (0.0 = fresh, 1.0 = exhausted).
    pub fn budget_used_fraction(&self) -> f64 {
        if self.budget_total == 0 {
            return 0.0;
        }
        let used = self.budget_total.saturating_sub(self.budget_remaining);
        used as f64 / self.budget_total as f64
    }

    /// Number of consecutive identical tool calls at the tail of history.
    ///
    /// Used by the Trajectory Regulation layer to detect repetition.
    pub fn consecutive_repetitions(&self) -> usize {
        let tool_names: Vec<&str> = self
            .history
            .iter()
            .flat_map(|m| m.tool_calls.iter())
            .map(|c| c.function.name.as_str())
            .collect();

        if tool_names.is_empty() {
            return 0;
        }
        let last = *tool_names.last().expect("non-empty");
        tool_names.iter().rev().take_while(|&&n| n == last).count()
    }
}
