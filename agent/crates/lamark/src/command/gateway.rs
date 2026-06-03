//! HTTP gateway server command.

use std::sync::Arc;

use lamark_config::Config;

/// Stub: gateway without args struct.
pub fn run(_cfg: Arc<Config>) {
    tracing::info!("gateway command called (stub)");
    println!("gateway command — not yet implemented");
}

/// Stub: gateway with args struct and load options.
pub fn run_with(
    _args: crate::cli::GatewayArgs,
    _opts: lamark_config::LoadOptions,
    _cfg: Arc<Config>,
) {
    tracing::info!("gateway command called (stub, with args)");
    println!("gateway command — not yet implemented");
}
