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

    /// Return the stable + context tiers as a cacheable prefix, plus volatile as separate part.
    /// Everything before `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` can use API-level global caching.
    /// Everything after must not be cached (session-specific).
    pub fn render_split(&self) -> (String, String) {
        let global_parts: Vec<&str> = [
            if !self.stable.is_empty() {
                Some(self.stable.trim())
            } else {
                None
            },
            if !self.context.is_empty() {
                Some(self.context.trim())
            } else {
                None
            },
        ]
        .into_iter()
        .flatten()
        .collect();

        let global = if global_parts.is_empty() {
            String::new()
        } else {
            global_parts.join("\n\n\n")
        };

        let dynamic = if self.volatile.is_empty() {
            String::new()
        } else {
            format!(
                "\n\n__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__\n\n{}",
                self.volatile.trim()
            )
        };

        (global, dynamic)
    }
}
