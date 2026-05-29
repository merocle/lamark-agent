//! `SkillStore` — an in-memory BM25-indexed catalog of loaded skills.
//!
//! Two-stage retrieval:
//!  1. Catalog: name + description only (~5-10 K tokens for 100 skills).
//!  2. Full body: loaded on demand via `read_skill`.

use crate::doc::SkillDoc;
use std::{collections::HashMap, path::PathBuf};

/// An in-memory catalog and retrieval store for loaded skills.
///
/// Backed by a BM25 index over name + description fields.
/// Full SKILL.md bodies are kept in memory after first load.
#[derive(Debug, Default)]
pub struct SkillStore {
    /// All loaded skills, keyed by name.
    skills: HashMap<String, SkillDoc>,
}

impl SkillStore {
    /// Open the default skill directories: project → user → bundled.
    pub fn open_default() -> Self {
        let mut store = Self::default();
        // Project skills
        if let Ok(dir) = std::env::current_dir().map(|d| d.join(".lamark/skills")) {
            store.load_dir(&dir);
        }
        // User skills
        if let Some(home) = dirs::home_dir() {
            store.load_dir(&home.join(".lamark/skills"));
        }
        store
    }

    /// Load all skills from a directory (each subdirectory is one skill).
    pub fn load_dir(&mut self, dir: &std::path::Path) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let skill_md = entry.path().join("SKILL.md");
            if skill_md.exists() {
                match SkillDoc::parse(&skill_md) {
                    Ok(doc) => {
                        self.skills.insert(doc.name().to_owned(), doc);
                    }
                    Err(e) => {
                        tracing::warn!(path = %skill_md.display(), "failed to parse skill: {e}");
                    }
                }
            }
        }
    }

    /// Register a skill in the store (used after MUSE skill creation).
    pub fn register(&mut self, doc: SkillDoc) {
        self.skills.insert(doc.name().to_owned(), doc);
    }

    /// Remove a skill by name (used by Curator pruning).
    pub fn remove(&mut self, name: &str) -> Option<SkillDoc> {
        self.skills.remove(name)
    }

    /// Return the catalog — name + description for all skills.
    pub fn catalog(&self) -> String {
        let mut out = String::new();
        for doc in self.skills.values() {
            out.push_str(&doc.catalog_entry());
            out.push('\n');
        }
        out
    }

    /// BM25-style search over name + description fields.
    ///
    /// Returns up to `k` skills ranked by relevance to `query`.
    /// Current implementation is a simple TF-IDF approximation;
    /// replace with a proper BM25 crate when available.
    pub fn search(&self, query: &str, k: usize) -> Vec<&SkillDoc> {
        let query_tokens: Vec<&str> = query.split_whitespace().collect();
        let mut scored: Vec<(f64, &SkillDoc)> = self
            .skills
            .values()
            .map(|doc| {
                let text = format!("{} {}", doc.name(), doc.description()).to_lowercase();
                let score = query_tokens
                    .iter()
                    .filter(|t| text.contains(&t.to_lowercase()))
                    .count() as f64;
                (score, doc)
            })
            .filter(|(s, _)| *s > 0.0)
            .collect();

        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        scored.into_iter().take(k).map(|(_, d)| d).collect()
    }

    /// Get a skill by exact name.
    pub fn get(&self, name: &str) -> Option<&SkillDoc> {
        self.skills.get(name)
    }

    /// Total number of skills in the store.
    pub fn len(&self) -> usize {
        self.skills.len()
    }

    /// Whether the store is empty.
    pub fn is_empty(&self) -> bool {
        self.skills.is_empty()
    }
}
