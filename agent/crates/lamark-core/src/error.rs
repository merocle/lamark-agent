//! Top-level error and result types shared across the Lamark workspace.

use thiserror::Error;

/// Top-level error type for the Lamark runtime.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum Error {
    /// An I/O error.
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),

    /// A generic error, typically wrapping a message from a dependency.
    #[error("{0}")]
    Other(String),
}

/// Convenience alias; every Lamark crate may use `lamark_core::Result`.
pub type Result<T, E = Error> = std::result::Result<T, E>;
