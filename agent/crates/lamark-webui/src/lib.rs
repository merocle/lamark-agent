//! Local browser control plane: axum HTTP server with an embedded SvelteKit SPA.
//!
//! Shares the same SQ/EQ event enums as the TUI; streams live session events
//! over WebSocket.
