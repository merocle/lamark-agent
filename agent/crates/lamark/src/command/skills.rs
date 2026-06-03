//! Skills management command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_args: crate::cli::SkillsArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::SkillsArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("skills command called (stub)");
    println!("skills command — not yet implemented");
}
