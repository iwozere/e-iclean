//! Tauri commands exposed to the webview. Thin by design (AGENTS.md §10): no business
//! logic lives here, everything forwards to the Python sidecar via `ipc.rs`.

use serde_json::Value;
use tauri::State;

use crate::ipc::IpcClient;

/// Generic JSON-RPC bridge: the JS side calls `invoke("backend_call", { method,
/// params })` for every IPC method in docs/ipc_protocol.md, rather than one bespoke
/// Tauri command per method.
#[tauri::command]
pub async fn backend_call(ipc: State<'_, IpcClient>, method: String, params: Value) -> Result<Value, Value> {
    ipc.call(&method, params).await
}
