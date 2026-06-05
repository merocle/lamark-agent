//! Remote control command.

use std::sync::Arc;

use lamark_config::Config;

#[allow(dead_code)]
pub fn run(_args: crate::cli::RemoteArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(
    _args: crate::cli::RemoteArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("remote command called (stub)");
    println!("remote command — not yet implemented");
}
