//! MCP server command.

use std::sync::Arc;

use lamark_config::Config;

pub fn run(_args: crate::cli::McpArgs, _cfg: Arc<Config>) {
    run_with(_args, Default::default(), _cfg);
}

pub fn run_with(_args: crate::cli::McpArgs, _opts: lamark_config::LoadOptions, _cfg: Arc<Config>) {
    tracing::info!("mcp command called (stub)");
    println!("mcp command — not yet implemented");
}
