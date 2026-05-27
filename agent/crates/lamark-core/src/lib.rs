//! Core types, traits, and primitives shared across all Lamark crates.
//!
//! `lamark-core` has no workspace dependencies — it is the bottom of the
//! dependency graph. Every other crate may import from here; nothing here
//! imports from a sibling crate.

mod error;

pub use error::{Error, Result};
