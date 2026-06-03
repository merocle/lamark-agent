//! Layered configuration loader for Lamark.
//!
//! Reads from compiled-in defaults, system file, user file, project file,
//! profile overlays, environment variables, and CLI flags — in that order.

mod config;
mod error;
mod loader;
mod types;

pub use config::validate;
pub use loader::{LoadOptions, expand_home, load, load_or_init};
pub use types::Config;
