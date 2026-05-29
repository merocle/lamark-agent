//! Runtime harness stack: the four LIFE-HARNESS lifecycle layers.
//!
//! Implements `lamark_core::harness::HarnessStack`. The binary (`lamark`)
//! constructs a `HarnessStack` from persisted evolution artifacts and injects
//! it into `AIAgent` at startup.
//!
//! Layer 1 — Environment Contract: evolved ΔC injected into system prompt.
//! Layer 2 — Procedural Skill:     BM25-retrieved skill documents injected.
//! Layer 3 — Action Realization:   tool calls validated before execution.
//! Layer 4 — Trajectory Regulation: degeneration detected and interrupted.
//!
//! See `docs/plan/10d-skillopt-life-harness.md` for full specification.

mod contract;
mod realization;
mod regulation;
mod skill_inject;
mod stack;

pub mod evolution;

pub use contract::{ContractDelta, EnvironmentContract};
pub use realization::{RealizationRule, RealizationRuleEngine};
pub use regulation::{RegulationConfig, TrajectoryRegulator};
pub use skill_inject::SkillInjector;
pub use stack::LiveHarnessStack;
