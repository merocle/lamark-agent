//! Validation helpers for merged configuration.

use crate::error::ConfigError;
use crate::types::Config;

/// Validate that a merged [`Config`] is internally consistent.
///
/// This is a no-op helper — basic validation happens at extraction time in
/// `load()`. Subclasses may override it for domain-specific checks.
pub const fn validate(_cfg: &Config) -> Result<(), ConfigError> {
    Ok(())
}
