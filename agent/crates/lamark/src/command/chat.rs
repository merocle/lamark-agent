//! Interactive chat and one-shot query command.

use std::sync::Arc;

use lamark_config::Config;

/// Stub: chat with args struct.
pub fn run(_args: crate::cli::ChatArgs, _cfg: Arc<Config>) {
    tracing::info!("chat command called (stub)");
    println!("chat command — not yet implemented");
}

/// Stub: chat with explicit load options.
pub fn run_with(_args: crate::cli::ChatArgs, _opts: lamark_config::LoadOptions, _cfg: Arc<Config>) {
    tracing::info!("chat command called (stub, with opts)");
    println!("chat command — not yet implemented");
}
