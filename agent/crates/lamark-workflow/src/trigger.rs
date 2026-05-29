//! Workflow trigger detection — when to switch from normal turn mode to
//! workflow planning mode.
//!
//! See `docs/plan/05e-dynamic-workflows.md §4`.

/// Determines whether a given user message or task warrants workflow mode.
pub struct WorkflowTrigger {
    /// Minimum number of distinct sub-goals detected to suggest workflow mode.
    pub complexity_threshold: usize,
    /// Whether the keyword "workflow" in the user message auto-activates.
    pub keyword_activation: bool,
}

impl Default for WorkflowTrigger {
    fn default() -> Self {
        Self {
            complexity_threshold: 5,
            keyword_activation: true,
        }
    }
}

impl WorkflowTrigger {
    /// Return `true` if the input should enter workflow planning mode.
    pub fn should_activate(&self, user_input: &str) -> bool {
        if self.keyword_activation && user_input.to_lowercase().contains("workflow") {
            return true;
        }
        // TODO: complexity heuristic (count distinct file paths, sub-goals, item lists)
        false
    }
}
