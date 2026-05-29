//! Layer 2 — Procedural Skill injection.
//!
//! Retrieves the top-k most relevant skills from the skill store using BM25
//! and injects their content into the system prompt.
//!
//! The `lamark-skills` crate owns `SkillStore`; this module is the harness
//! side of the integration.

use lamark_core::turn::TurnContext;
use lamark_skills::store::SkillStore;

/// Injects retrieved skills into the system prompt.
#[derive(Debug)]
pub struct SkillInjector {
    store: SkillStore,
    /// Number of skills to retrieve per turn.
    pub k: usize,
}

impl SkillInjector {
    /// Create a new injector backed by the given skill store.
    pub fn new(store: SkillStore) -> Self {
        Self { store, k: 3 }
    }

    /// With a custom k value.
    pub fn with_k(mut self, k: usize) -> Self {
        self.k = k;
        self
    }

    /// Retrieve relevant skills and append them to `system_prompt`.
    pub fn inject(&self, ctx: &TurnContext<'_>, system_prompt: &mut String) {
        // Build a query from the latest user message.
        let query = ctx
            .history
            .iter()
            .rev()
            .find(|m| matches!(m.role, lamark_core::message::Role::User))
            .map(|m| m.content.as_str())
            .unwrap_or("");

        if query.is_empty() {
            return;
        }

        let skills = self.store.search(query, self.k);
        if skills.is_empty() {
            return;
        }

        system_prompt.push_str("\n\n---\n\n## Available skills\n\n");
        for skill in &skills {
            system_prompt.push_str(&format!("### {}\n\n{}\n\n", skill.name(), skill.body));
            if let Some(memory) = &skill.memory {
                if !memory.is_empty() {
                    system_prompt.push_str(&format!("**Skill memory:**\n\n{}\n\n", memory));
                }
            }
        }
    }
}
