//! Layer 1 — Environment Contract.
//!
//! Maintains the base contract (stable tool descriptions) and an evolved
//! delta (ΔC), derived weekly from trace failure analysis. Both are rendered
//! into the system prompt before the first LLM call.

use serde::{Deserialize, Serialize};

/// The stable base contract — tool schemas, admissible actions, core policies.
/// Rarely modified by hand; updated when tool APIs change.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EnvironmentContract {
    /// Rendered markdown block injected into the system prompt.
    pub base: String,
    /// Evolved delta from trace failures (refreshed weekly by `harness_evolve`).
    pub delta: ContractDelta,
}

impl EnvironmentContract {
    /// Render the contract into a single string for system-prompt injection.
    pub fn render(&self) -> String {
        if self.delta.content.is_empty() {
            return self.base.clone();
        }
        format!(
            "{}\n\n---\n\n## Environment notes (evolved)\n\n{}",
            self.base, self.delta.content
        )
    }
}

/// The evolved portion of the contract — derived from trace failure analysis.
///
/// Written weekly by `harness_evolve.py`; stored in
/// `~/.lamark/harness/contract_delta.md` and loaded at startup.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ContractDelta {
    /// Markdown content to append to the base contract.
    pub content: String,
    /// Semantic version of this delta for cache-busting.
    pub version: u32,
    /// ISO-8601 timestamp of the last evolution run.
    pub evolved_at: Option<String>,
}

impl ContractDelta {
    /// Load from a markdown file written by the Python evolution script.
    pub fn load(path: &std::path::Path) -> lamark_core::Result<Self> {
        let content = std::fs::read_to_string(path).map_err(lamark_core::Error::Io)?;
        Ok(Self {
            content,
            version: 0,
            evolved_at: None,
        })
    }
}
