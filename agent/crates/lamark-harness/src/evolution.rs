//! Harness evolution entry point — types used by the Python training pipeline.
//!
//! The Python side (`learning/scripts/harness_evolve.py`) reads failure traces
//! and writes updated `contract_delta.md`, `realization_rules.toml`, and
//! `regulation_rules.toml`. This module defines the report types serialised
//! to JSON for KB upload after each evolution run.
//!
//! See `docs/plan/10d-skillopt-life-harness.md §3.1`.

use serde::{Deserialize, Serialize};

/// Summary of one harness evolution run, serialised to JSON for the KB.
#[derive(Debug, Serialize, Deserialize)]
pub struct EvolutionReport {
    pub evolved_at: String,
    pub traces_examined: usize,
    pub failure_counts: FailureCounts,
    pub contract_updated: bool,
    pub realization_updated: bool,
    pub regulation_updated: bool,
    pub new_skills: usize,
}

/// Failure type counts from annotated traces.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct FailureCounts {
    pub action_realization: usize,
    pub contract_mismatch: usize,
    pub trajectory_degeneration: usize,
    pub reasoning: usize,
}

impl FailureCounts {
    /// Failures routed to harness evolution (not SFT).
    pub fn harness_total(&self) -> usize {
        self.action_realization + self.contract_mismatch + self.trajectory_degeneration
    }
}
