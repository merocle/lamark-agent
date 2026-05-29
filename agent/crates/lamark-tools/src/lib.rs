//! Tool registry and built-in tool implementations.
//!
//! Contains the ~70 built-in tools organised into ~28 toolsets (file I/O,
//! shell, search, code analysis, etc.).
//!
//! Subsystems:
//! - `traits`       — `Tool` trait + `ToolCapabilities`, `ToolSchema`
//! - `registry`     — `ToolRegistry` (name → Arc<dyn Tool>)
//! - `skill_create` — MUSE `skill_create` tool
//!
//! See `docs/plan/05-layer-4-agent-core.md`.

pub mod registry;
pub mod skill_create;
pub mod traits;

pub use registry::ToolRegistry;
pub use traits::{Tool, ToolCapabilities, ToolSchema};
