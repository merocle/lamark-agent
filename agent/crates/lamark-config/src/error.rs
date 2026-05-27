//! Configuration error type.

use thiserror::Error;

/// An error that can occur while loading or validating Lamark configuration.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum ConfigError {
    /// The config file could not be read or parsed.
    #[error("figment: {0}")]
    Figment(#[from] figment::Error),

    /// The config failed semantic validation.
    #[error("invalid config: {0}")]
    Invalid(String),

    /// A required path (config dir, trace root, etc.) could not be resolved.
    #[error("path error: {0}")]
    Path(String),

    /// An I/O error accessing a config file.
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
}
