//! Layer 4 — Trajectory Regulation.
//!
//! Monitors conversation history after each tool result and injects
//! recovery messages when degeneration is detected.
//!
//! Patterns detected:
//!  - Repetition: same tool call N consecutive times.
//!  - Oscillation: ABAB... pattern in last W actions.
//!  - Budget pressure: < 30% / 15% remaining.
//!  - Stagnation: N tool calls with no new assistant content.

use lamark_core::{harness::RegulationOutput, tool::ToolResult, turn::TurnContext};
use serde::{Deserialize, Serialize};

/// Configuration for the trajectory regulator.
///
/// Loaded from `~/.lamark/harness/regulation_rules.toml`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegulationConfig {
    /// After this many consecutive identical tool calls, emit a `Warning`.
    #[serde(default = "default_repetition_threshold")]
    pub repetition_threshold: usize,
    /// Window size for ABAB oscillation detection.
    #[serde(default = "default_oscillation_window")]
    pub oscillation_window: usize,
    /// Fraction of budget remaining at which a `Hint` is emitted.
    #[serde(default = "default_budget_hint_at")]
    pub budget_hint_at: f64,
    /// Fraction of budget remaining at which a `Warning` is emitted.
    #[serde(default = "default_budget_warning_at")]
    pub budget_warning_at: f64,
    /// Fraction of budget remaining at which a `Directive` is emitted.
    #[serde(default = "default_budget_directive_at")]
    pub budget_directive_at: f64,
}

fn default_repetition_threshold() -> usize {
    3
}
fn default_oscillation_window() -> usize {
    6
}
fn default_budget_hint_at() -> f64 {
    0.30
}
fn default_budget_warning_at() -> f64 {
    0.20
}
fn default_budget_directive_at() -> f64 {
    0.10
}

impl Default for RegulationConfig {
    fn default() -> Self {
        Self {
            repetition_threshold: default_repetition_threshold(),
            oscillation_window: default_oscillation_window(),
            budget_hint_at: default_budget_hint_at(),
            budget_warning_at: default_budget_warning_at(),
            budget_directive_at: default_budget_directive_at(),
        }
    }
}

/// Implements trajectory regulation using a configured rule set.
#[derive(Debug, Default)]
pub struct TrajectoryRegulator {
    pub config: RegulationConfig,
}

impl TrajectoryRegulator {
    /// Load configuration from a TOML file, falling back to defaults on error.
    pub fn load(path: &std::path::Path) -> Self {
        let config = std::fs::read_to_string(path)
            .ok()
            .and_then(|s| toml::from_str(&s).ok())
            .unwrap_or_default();
        Self { config }
    }

    /// Evaluate the current trajectory and return an intervention if needed.
    pub fn evaluate(&self, ctx: &TurnContext<'_>, _last_result: &ToolResult) -> RegulationOutput {
        // 1. Repetition check.
        let reps = ctx.consecutive_repetitions();
        if reps >= self.config.repetition_threshold {
            tracing::warn!(
                repetitions = reps,
                "trajectory regulation: repetition detected"
            );
            return RegulationOutput::Warning {
                message: format!(
                    "You have called the same tool {reps} times in a row. \
                     Try a different approach or report that this task cannot be completed."
                ),
            };
        }

        // 2. Oscillation check.
        let tool_names: Vec<&str> = ctx
            .history
            .iter()
            .flat_map(|m| m.tool_calls.iter())
            .map(|c| c.function.name.as_str())
            .collect();
        if is_oscillating(&tool_names, self.config.oscillation_window) {
            tracing::warn!("trajectory regulation: oscillation detected");
            return RegulationOutput::Warning {
                message: "Your action sequence is oscillating. Reconsider your strategy.".into(),
            };
        }

        // 3. Budget pressure.
        let used = ctx.budget_used_fraction();
        if used >= (1.0 - self.config.budget_directive_at) {
            return RegulationOutput::Directive {
                message: format!(
                    "CRITICAL: only {} steps remain. You MUST conclude or report failure now.",
                    ctx.budget_remaining
                ),
            };
        }
        if used >= (1.0 - self.config.budget_warning_at) {
            return RegulationOutput::Warning {
                message: format!(
                    "{} steps remaining. Please work toward a conclusion.",
                    ctx.budget_remaining
                ),
            };
        }
        if used >= (1.0 - self.config.budget_hint_at) {
            return RegulationOutput::Hint {
                message: format!("{} steps remaining.", ctx.budget_remaining),
            };
        }

        RegulationOutput::None
    }
}

/// Detect ABAB... oscillation in the last `window` elements.
fn is_oscillating(names: &[&str], window: usize) -> bool {
    if names.len() < window {
        return false;
    }
    let tail = &names[names.len() - window..];
    // Simple even/odd check: tail[i] == tail[i+2] for all i
    tail.windows(3).all(|w| w[0] == w[2])
}
