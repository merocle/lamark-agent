//! MUSE skill registration gate — run tests before accepting a new skill.
//!
//! New skills created by `skill_create` are only registered into the store
//! after all `tests/` pytest files pass in a sandbox. This enforces the MUSE
//! invariant: untested skills are never deployed.
//!
//! See `docs/plan/10d-skillopt-life-harness.md §8.3`.

use crate::store::SkillStore;
use std::path::Path;

/// Outcome of the registration gate.
#[derive(Debug)]
pub enum RegistrationOutcome {
    Registered { name: String },
    TestsFailed { name: String, errors: Vec<String> },
    RegisteredNoTests { name: String },
}

/// Register a skill after running its test suite.
///
/// `sandbox_run_tests` is provided by the caller (typically `lamark-sandbox`)
/// so that `lamark-skills` does not depend on the sandbox crate.
pub fn register_skill<F>(
    _skill_dir: &Path,
    _store: &mut SkillStore,
    _sandbox_run_tests: F,
) -> lamark_core::Result<RegistrationOutcome>
where
    F: FnOnce(&Path) -> (bool, Vec<String>),
{
    // TODO: implement (parse SKILL.md, run tests/, accept or reject)
    Err(lamark_core::Error::Other(
        "register_skill not yet implemented".into(),
    ))
}
