//! System prompt construction for Lamark, inspired by hermes-agent's three-tier la-collection logic.

/// The full system prompt assembled from stable, context, and volatile tiers.
#[derive(Debug, Clone, Default)]
pub struct SystemPrompt {
    pub stable: String,
    pub context: String,
    pub volatile: String,
}

impl SystemPrompt {
    /// Joins the three tiers into a single string with `\n\n` separators.
    pub fn render(&self) -> String {
        let parts = [
            self.stable.as_str(),
            self.context.as_str(),
            self.volatile.as_str(),
        ];

        parts
            .iter()
            .filter(|p| !p.is_empty())
            .map(|p| p.trim())
            .collect::<Vec<_>>()
            .join("\n\n")
    }
}
