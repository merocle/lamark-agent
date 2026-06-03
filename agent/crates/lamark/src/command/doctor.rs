//! Doctor validation command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_cfg: Arc<Config>) {
    tracing::info!("doctor command called (stub)");
    println!("doctor command — not yet implemented");
}
