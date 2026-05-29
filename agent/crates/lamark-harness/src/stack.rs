//! The canonical `HarnessStack` implementation combining all four layers.

use std::sync::Arc;

use lamark_core::{
    harness::{HarnessStack, RealizationDecision, RegulationOutput},
    tool::{ToolCall, ToolResult},
    turn::TurnContext,
};

use crate::{
    contract::EnvironmentContract, realization::RealizationRuleEngine,
    regulation::TrajectoryRegulator, skill_inject::SkillInjector,
};

/// The live harness stack wiring all four LIFE-HARNESS layers together.
///
/// Constructed by the binary at startup from persisted evolution artifacts.
/// Swap out `lamark_core::harness::PassthroughHarness` for this in production.
pub struct LiveHarnessStack {
    contract: EnvironmentContract,
    skill_injector: SkillInjector,
    realization: RealizationRuleEngine,
    regulation: TrajectoryRegulator,
}

impl LiveHarnessStack {
    /// Construct a live stack from evolution artifacts on disk.
    ///
    /// Missing files fall back to safe defaults (no-op for each layer).
    pub fn from_artifacts(artifacts_dir: &std::path::Path) -> Self {
        let contract = {
            let base_path = artifacts_dir.join("contract_base.md");
            let delta_path = artifacts_dir.join("contract_delta.md");
            let base = std::fs::read_to_string(&base_path).unwrap_or_default();
            let delta = if delta_path.exists() {
                crate::contract::ContractDelta::load(&delta_path).unwrap_or_default()
            } else {
                Default::default()
            };
            EnvironmentContract { base, delta }
        };

        let realization =
            RealizationRuleEngine::load(&artifacts_dir.join("realization_rules.toml"));
        let regulation = TrajectoryRegulator::load(&artifacts_dir.join("regulation_rules.toml"));

        let skill_store = lamark_skills::store::SkillStore::open_default();
        let skill_injector = SkillInjector::new(skill_store);

        Self {
            contract,
            skill_injector,
            realization,
            regulation,
        }
    }
}

impl HarnessStack for LiveHarnessStack {
    fn prepare_context(&self, ctx: &TurnContext<'_>, system_prompt: &mut String) {
        // Layer 1: inject evolved contract delta.
        let contract_block = self.contract.render();
        if !contract_block.is_empty() {
            system_prompt.push_str("\n\n");
            system_prompt.push_str(&contract_block);
        }
        // Layer 2: inject retrieved skills.
        self.skill_injector.inject(ctx, system_prompt);
    }

    fn realize_action(&self, call: &ToolCall, ctx: &TurnContext<'_>) -> RealizationDecision {
        self.realization.evaluate(call, ctx)
    }

    fn regulate_trajectory(
        &self,
        ctx: &TurnContext<'_>,
        last_result: &ToolResult,
    ) -> RegulationOutput {
        self.regulation.evaluate(ctx, last_result)
    }
}
