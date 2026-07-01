//! Event names forwarded to the webview (docs/ipc_protocol.md "Events" section).
//!
//! Sidecar-originated notifications (`device_connected`, `transfer_progress`, etc.)
//! are forwarded verbatim by `sidecar.rs` using the `event` field from the JSON-RPC
//! message - no per-event Rust type is needed since the payload just passes through
//! to `serde_json::Value` on its way to the webview. The one Rust-originated event is
//! defined here as a constant so `sidecar.rs` and any future emitter stay in sync.

/// Emitted when the sidecar's stdout pipe closes unexpectedly (process crashed or was
/// killed) - the UI must show a real error state, never sit in a spinner.
pub const BACKEND_CRASHED: &str = "backend_crashed";
