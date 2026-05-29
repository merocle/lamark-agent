//! Per-skill `.memory.md` writer — append-only experience log.
//!
//! The memory file accumulates usage notes, failure modes, and edge cases
//! across sessions. It is loaded alongside SKILL.md at runtime but is
//! NEVER included in cross-agent skill transfers.
//!
//! See `docs/plan/10d-skillopt-life-harness.md §8.6`.

use std::path::Path;

/// Append a timestamped entry to a skill's `.memory.md`.
///
/// Creates the file if it does not exist.
pub fn append_skill_memory(_skill_dir: &Path, _content: &str) -> lamark_core::Result<()> {
    // TODO: implement (chrono timestamp + OpenOptions append)
    Ok(())
}

/// Read raw `.memory.md` content (returns `None` if file absent).
pub fn read_skill_memory(_skill_dir: &Path) -> Option<String> {
    // TODO: implement
    None
}

/// Return all paths in a skill directory EXCEPT `.memory.md`.
///
/// Used when transferring a skill to another agent.
pub fn transferable_paths(_skill_dir: &Path) -> Vec<std::path::PathBuf> {
    // TODO: implement (filter .memory.md)
    vec![]
}
