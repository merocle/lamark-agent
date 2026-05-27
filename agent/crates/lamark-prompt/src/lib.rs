//! Hierarchical system-prompt composer.
//!
//! Layers: `override` → `coordinator` → `agent` → `custom` → `default` → `append`.
//! Produces a stable, cache-friendly prompt section model.
