//! JSON-RPC-over-stdio framing: request/response matching and notification routing.
//! Mirrors docs/ipc_protocol.md exactly - keep both in sync when this changes.

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;
use tokio::sync::{mpsc, oneshot, Mutex};

pub type PendingMap = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, Value>>>>>;

fn internal_error(message: &str) -> Value {
    serde_json::json!({ "code": "backend_internal", "message": message })
}

/// Sends JSON-RPC requests to the sidecar and awaits the matching response.
/// Cheap to clone: internally just an mpsc sender + a shared pending-request table.
#[derive(Clone)]
pub struct IpcClient {
    stdin_tx: mpsc::UnboundedSender<String>,
    pending: PendingMap,
}

impl IpcClient {
    pub fn new(stdin_tx: mpsc::UnboundedSender<String>, pending: PendingMap) -> Self {
        Self { stdin_tx, pending }
    }

    /// Send a request and await its response. Returns `Err` with a mapped error
    /// object (see docs/ipc_protocol.md) on backend failure, disconnection, or crash.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, Value> {
        let id = uuid::Uuid::new_v4().to_string();
        let (tx, rx) = oneshot::channel();
        {
            let mut pending = self.pending.lock().await;
            pending.insert(id.clone(), tx);
        }

        let request = serde_json::json!({ "id": id, "method": method, "params": params });

        if self.stdin_tx.send(request.to_string()).is_err() {
            self.pending.lock().await.remove(&id);
            return Err(internal_error("The backend process is not running."));
        }

        rx.await
            .unwrap_or_else(|_| Err(internal_error("The backend process stopped responding.")))
    }
}

/// Route one decoded line from the sidecar's stdout.
///
/// If the line is a response to a pending request, resolves it in place and returns
/// `None`. If it's a notification, returns `Some((event, data))` so the caller can
/// forward it as a Tauri event. Malformed lines are ignored (returns `None`).
pub async fn route_incoming_line(pending: &PendingMap, line: &str) -> Option<(String, Value)> {
    let message: Value = serde_json::from_str(line).ok()?;

    if let Some(id) = message.get("id").and_then(Value::as_str) {
        if let Some(tx) = pending.lock().await.remove(id) {
            let result = match message.get("error") {
                Some(err) => Err(err.clone()),
                None => Ok(message.get("result").cloned().unwrap_or(Value::Null)),
            };
            let _ = tx.send(result);
        }
        return None;
    }

    let event = message.get("event").and_then(Value::as_str)?;
    let data = message.get("data").cloned().unwrap_or(Value::Null);
    Some((event.to_string(), data))
}
