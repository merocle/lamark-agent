//! Layered configuration loader for Lamark.

mod config;
mod error;
mod loader;
mod types;

pub use config::validate;
pub use loader::{expand_home, load, load_or_init};
pub use types::{AgentConfig, Config, LoadOptions, ModelConfig, SafetyMode, ToolUseEnforcement};
