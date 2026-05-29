//! Layer 3 — Action Realization.
//!
//! Validates tool calls before sandbox execution. Returns `Exec` to proceed
//! or `Block { message }` to feed a correction back to the model.
//!
//! Rules are loaded from `~/.lamark/harness/realization_rules.toml`,
//! generated weekly by `harness_evolve.py` from ACTION_REALIZATION failures.

use lamark_core::{
    harness::{RealizationDecision, RegulationOutput},
    tool::ToolCall,
    turn::TurnContext,
};
use serde::{Deserialize, Serialize};

/// A single realization rule loaded from TOML configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RealizationRule {
    /// Tool name this rule applies to (`*` = all tools).
    pub tool: String,
    /// Human-readable description for audit logs.
    pub description: String,
    /// Condition expression (simple DSL, evaluated against call args).
    pub condition: RuleCondition,
    /// Message returned to the model when the condition triggers.
    pub block_message: String,
    /// Whether to log a warning when this rule fires.
    #[serde(default = "default_true")]
    pub log_violation: bool,
}

/// Condition variants for realization rules.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RuleCondition {
    /// Block if the named argument is absent.
    MissingArg { name: String },
    /// Block if the named argument does not match a regex.
    ArgFormat { name: String, pattern: String },
    /// Block if the call is an exact duplicate of the previous call.
    DuplicateCall,
    /// Block if the tool has been called more than `max` times this turn.
    TooManyCallsThisTurn { max: usize },
    /// Block if `arg_name` is present and its value exceeds `max`.
    ArgExceedsMax { arg_name: String, max: i64 },
}

fn default_true() -> bool {
    true
}

/// Evaluates `RealizationRule`s against incoming tool calls.
#[derive(Debug, Default)]
pub struct RealizationRuleEngine {
    rules: Vec<RealizationRule>,
}

impl RealizationRuleEngine {
    /// Load rules from a TOML file.
    pub fn load(path: &std::path::Path) -> Self {
        let rules = std::fs::read_to_string(path)
            .ok()
            .and_then(|s| toml::from_str::<Vec<RealizationRule>>(&s).ok())
            .unwrap_or_default();
        Self { rules }
    }

    /// Evaluate all applicable rules against a tool call.
    ///
    /// Returns the first blocking decision found, or `Exec` if no rule fires.
    pub fn evaluate(&self, call: &ToolCall, ctx: &TurnContext<'_>) -> RealizationDecision {
        for rule in &self.rules {
            if rule.tool != "*" && rule.tool != call.function.name {
                continue;
            }
            if self.condition_fires(&rule.condition, call, ctx) {
                if rule.log_violation {
                    tracing::warn!(
                        tool = %call.function.name,
                        rule = %rule.description,
                        "action realization rule fired"
                    );
                }
                return RealizationDecision::Block {
                    message: rule.block_message.clone(),
                };
            }
        }
        RealizationDecision::Exec
    }

    fn condition_fires(
        &self,
        cond: &RuleCondition,
        call: &ToolCall,
        ctx: &TurnContext<'_>,
    ) -> bool {
        match cond {
            RuleCondition::MissingArg { name } => {
                let Ok(args) = call.decode_args::<serde_json::Value>() else {
                    return false;
                };
                args.get(name).is_none()
            }
            RuleCondition::ArgFormat { name, pattern } => {
                let Ok(args) = call.decode_args::<serde_json::Value>() else {
                    return false;
                };
                let Some(val) = args.get(name).and_then(|v| v.as_str()) else {
                    return false;
                };
                let Ok(re) = regex::Regex::new(pattern) else {
                    return false;
                };
                !re.is_match(val)
            }
            RuleCondition::DuplicateCall => {
                ctx.history
                    .iter()
                    .flat_map(|m| m.tool_calls.iter())
                    .filter(|c| {
                        c.function.name == call.function.name
                            && c.function.arguments == call.function.arguments
                    })
                    .count()
                    > 0
            }
            RuleCondition::TooManyCallsThisTurn { max } => {
                ctx.history
                    .iter()
                    .flat_map(|m| m.tool_calls.iter())
                    .filter(|c| c.function.name == call.function.name)
                    .count()
                    >= *max
            }
            RuleCondition::ArgExceedsMax { arg_name, max } => {
                let Ok(args) = call.decode_args::<serde_json::Value>() else {
                    return false;
                };
                args.get(arg_name)
                    .and_then(|v| v.as_i64())
                    .map_or(false, |v| v > *max)
            }
        }
    }
}
