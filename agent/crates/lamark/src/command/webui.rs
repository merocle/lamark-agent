//! Web UI server command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_args: crate::cli::WebuiArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::WebuiArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("webui command called (stub)");
    println!("webui command — not yet implemented");
}
