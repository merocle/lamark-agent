//! Sub-agent command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_args: crate::cli::AgentArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::AgentArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("agent command called (stub)");
    println!("agent command — not yet implemented");
}
