//! Skill loader, YAML-frontmatter parser, SkillStore, Curator, and MUSE registration gate.
//!
//! Discovery order: project `.lamark/skills/` → user `~/.lamark/skills/` → bundled skills.
//!
//! Subsystems:
//! - `doc`      — `SkillDoc` (SKILL.md parser, YAML frontmatter)
//! - `store`    — `SkillStore` (BM25 catalog + retrieval)
//! - `memory`   — `.memory.md` append-only experience log (per-skill, per-agent)
//! - `register` — MUSE registration gate (tests must pass before deploy)
//! - `curator`  — SkillOpt Curator loop (weekly validation-gated optimization)
//!
//! See `docs/plan/08-layer-7-skills-plugins-curator.md` and
//! `docs/plan/10d-skillopt-life-harness.md`.

pub mod curator;
pub mod doc;
pub mod memory;
pub mod register;
pub mod store;

pub use doc::SkillDoc;
pub use store::SkillStore;
