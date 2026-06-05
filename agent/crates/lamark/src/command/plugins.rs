//! Plugins management command.

use std::sync::Arc;

use lamark_config::Config;

#[allow(dead_code)]
pub fn run(_args: crate::cli::PluginsArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::PluginsArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("plugins command called (stub)");
    println!("plugins command — not yet implemented");
}
