//! SkillOpt Curator — text-space optimizer for skill documents.
//!
//! Implements the SkillOpt algorithm (arXiv:2605.23904) as Lamark's weekly
//! Curator run. For each domain skill, runs a validation-gated optimization
//! loop: rollout batches → reflection → bounded edits → held-out validation.
//!
//! See `docs/plan/08-layer-7-skills-plugins-curator.md` and
//! `docs/plan/10d-skillopt-life-harness.md §2`.

use serde::{Deserialize, Serialize};

/// Configuration for one SkillOpt run on a single skill.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillOptConfig {
    pub epochs: usize,                    // default: 4
    pub rollout_batch_size: usize,        // default: 40
    pub reflection_minibatch_size: usize, // default: 8
    pub edit_budget: usize,               // default: 4 (Lt, cosine decay)
    pub edit_budget_floor: usize,         // default: 2
    pub slow_update: bool,                // default: true
    pub slow_update_sample_size: usize,   // default: 20
}

impl Default for SkillOptConfig {
    fn default() -> Self {
        Self {
            epochs: 4,
            rollout_batch_size: 40,
            reflection_minibatch_size: 8,
            edit_budget: 4,
            edit_budget_floor: 2,
            slow_update: true,
            slow_update_sample_size: 20,
        }
    }
}

/// A proposed edit to a skill document.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum SkillEdit {
    Add {
        content: String,
        after_heading: Option<String>,
    },
    Delete {
        heading: String,
    },
    Replace {
        heading: String,
        new_content: String,
    },
}

/// Persistent run state (serialised between epochs).
#[derive(Debug, Serialize, Deserialize)]
pub struct CuratorRunState {
    pub skill_name: String,
    pub current_skill: String,
    pub best_skill: String,
    pub current_score: f64,
    pub best_score: f64,
    pub epoch: usize,
    pub rejected_buffer: Vec<RejectedEdit>,
    pub meta_skill: Option<String>, // optimizer-side only; NOT deployed
}

/// A rejected edit plus the score drop it caused.
#[derive(Debug, Serialize, Deserialize)]
pub struct RejectedEdit {
    pub edits: Vec<SkillEdit>,
    pub score_before: f64,
    pub score_after: f64,
}
