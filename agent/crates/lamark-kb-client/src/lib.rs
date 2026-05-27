//! HTTP client for the sibling `knowledge-base` service.
//!
//! All calls have a 5-second timeout. Writes are fire-and-forget with a local
//! SQLite spool; reads degrade to the SQLite fallback when KB is unreachable.
