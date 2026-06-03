//! Config management command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_args: crate::cli::ConfigArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::ConfigArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("config command called (stub)");
    println!("config command — not yet implemented");
}
