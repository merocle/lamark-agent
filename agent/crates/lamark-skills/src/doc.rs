//! `SkillDoc` — the in-memory representation of a parsed skill file.
//!
//! A skill lives on disk as `<skill-name>/SKILL.md` (YAML frontmatter + body)
//! alongside optional `.memory.md`, `scripts/`, `tests/`, and `resources/`.
//! This module parses and owns the in-memory form.

use serde::{Deserialize, Serialize};

/// Parsed frontmatter from a `SKILL.md` file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillFrontmatter {
    /// Kebab-case skill identifier; must match the directory name.
    pub name: String,
    /// One-paragraph description used for catalog indexing and skill selection.
    pub description: String,
    /// Human-readable version string (semver encouraged).
    #[serde(default)]
    pub version: String,
    /// Author: `"agent"`, `"user"`, or `"bundled"`.
    #[serde(default = "default_author")]
    pub author: String,
    /// Whether this skill is exposed as a `/name` slash command.
    #[serde(default)]
    pub is_command: bool,
    /// Tools required for this skill to execute.
    #[serde(default)]
    pub tools_required: Vec<String>,
    /// If true, the Curator may rewrite this skill via SkillOpt.
    #[serde(default = "default_mutable")]
    pub mutable: bool,
    /// If true, this skill is pinned — exempt from Curator pruning.
    #[serde(default)]
    pub pinned: bool,
}

fn default_author() -> String {
    "agent".into()
}
fn default_mutable() -> bool {
    true
}

/// In-memory representation of a fully parsed skill.
#[derive(Debug, Clone)]
pub struct SkillDoc {
    /// Parsed frontmatter.
    pub frontmatter: SkillFrontmatter,
    /// Markdown body (everything after the closing `---`).
    pub body: String,
    /// Contents of `.memory.md` if it exists, otherwise `None`.
    pub memory: Option<String>,
    /// Filesystem path to the skill directory.
    pub dir: std::path::PathBuf,
}

impl SkillDoc {
    /// Short name accessor.
    pub fn name(&self) -> &str {
        &self.frontmatter.name
    }

    /// Description accessor.
    pub fn description(&self) -> &str {
        &self.frontmatter.description
    }

    /// Render catalog entry (name + description only; used for prompt injection).
    pub fn catalog_entry(&self) -> String {
        format!(
            "- **{}**: {}",
            self.frontmatter.name, self.frontmatter.description
        )
    }

    /// Parse a `SKILL.md` file at the given path.
    ///
    /// Expects YAML frontmatter delimited by `---` lines.
    pub fn parse(skill_md_path: &std::path::Path) -> lamark_core::Result<Self> {
        let content = std::fs::read_to_string(skill_md_path).map_err(lamark_core::Error::Io)?;

        let (frontmatter, body) = split_frontmatter(&content)?;

        let dir = skill_md_path
            .parent()
            .unwrap_or(std::path::Path::new("."))
            .to_path_buf();

        let memory = {
            let memory_path = dir.join(".memory.md");
            if memory_path.exists() {
                Some(std::fs::read_to_string(&memory_path).unwrap_or_default())
            } else {
                None
            }
        };

        Ok(Self {
            frontmatter,
            body,
            memory,
            dir,
        })
    }
}

/// Split YAML frontmatter (between `---` delimiters) from body.
fn split_frontmatter(content: &str) -> lamark_core::Result<(SkillFrontmatter, String)> {
    let lines: Vec<&str> = content.lines().collect();
    if lines.first().map(|l| l.trim()) != Some("---") {
        return Err(lamark_core::Error::Other(
            "SKILL.md missing opening ---".into(),
        ));
    }
    let close = lines[1..]
        .iter()
        .position(|l| l.trim() == "---")
        .ok_or_else(|| lamark_core::Error::Other("SKILL.md missing closing ---".into()))?;
    let yaml = lines[1..=close].join("\n");
    let body = lines[close + 2..].join("\n");
    let fm: SkillFrontmatter = serde_yaml::from_str(&yaml)
        .map_err(|e| lamark_core::Error::Other(format!("frontmatter parse error: {e}")))?;
    Ok((fm, body))
}
