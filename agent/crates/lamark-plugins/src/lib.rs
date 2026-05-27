//! Plugin host: capability-gated WASM (wasmtime) and native dylib (libloading).
//!
//! This crate is the only one in the workspace that requires `unsafe` code —
//! see `allow(unsafe_code)` below.
#![allow(unsafe_code)]
