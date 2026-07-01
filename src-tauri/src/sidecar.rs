//! Spawns and owns the Python backend sidecar process (docs/ipc_protocol.md).

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;

use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, Mutex};

use crate::events::BACKEND_CRASHED;
use crate::ipc::{route_incoming_line, IpcClient, PendingMap};

/// Dev-mode sidecar location: the project's `backend/` folder with its own venv,
/// resolved relative to the `src-tauri` crate directory (the cwd under `cargo tauri
/// dev`). Production must instead resolve a frozen PyInstaller executable from the
/// app's resource directory - not yet implemented, see docs/project_specification.md
/// §5.8 (installer/bundling task, tracked as a follow-up, not silently assumed done).
fn dev_python_command() -> (PathBuf, PathBuf, PathBuf) {
    let backend_dir = PathBuf::from("..").join("backend");
    let python_exe = backend_dir.join(".venv").join("Scripts").join("python.exe");
    let run_script = backend_dir.join("run.py");
    (backend_dir, python_exe, run_script)
}

/// Spawn the sidecar and wire up its stdio: a writer task drains outgoing JSON-RPC
/// requests to stdin, a reader task parses stdout lines into responses/notifications,
/// and stderr (the sidecar's own human-readable log) is forwarded into the Rust log.
///
/// Returns the `IpcClient` for issuing requests and the `Child` handle so the caller
/// can terminate the process on app exit.
pub async fn spawn_sidecar(app: AppHandle) -> Result<(IpcClient, Child), String> {
    let (backend_dir, python_exe, run_script) = dev_python_command();

    let mut child = Command::new(&python_exe)
        .arg(&run_script)
        .current_dir(&backend_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to spawn backend sidecar ({}): {}", python_exe.display(), e))?;

    let stdin = child.stdin.take().expect("sidecar stdin was not piped");
    let stdout = child.stdout.take().expect("sidecar stdout was not piped");
    let stderr = child.stderr.take().expect("sidecar stderr was not piped");

    let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));
    let (stdin_tx, mut stdin_rx) = mpsc::unbounded_channel::<String>();

    // Writer task: forward outgoing JSON-RPC lines to the sidecar's stdin.
    tokio::spawn(async move {
        let mut stdin = stdin;
        while let Some(line) = stdin_rx.recv().await {
            if stdin.write_all(line.as_bytes()).await.is_err() {
                break;
            }
            if stdin.write_all(b"\n").await.is_err() {
                break;
            }
            if stdin.flush().await.is_err() {
                break;
            }
        }
    });

    // Reader task: parse stdout lines, resolve pending calls, forward notifications.
    {
        let pending = pending.clone();
        let app = app.clone();
        tokio::spawn(async move {
            let mut reader = BufReader::new(stdout).lines();
            loop {
                match reader.next_line().await {
                    Ok(Some(line)) => {
                        if let Some((event, data)) = route_incoming_line(&pending, &line).await {
                            let _ = app.emit(&event, data);
                        }
                    }
                    Ok(None) => {
                        log::warn!("sidecar: stdout closed, backend process likely exited");
                        let _ = app.emit(BACKEND_CRASHED, serde_json::json!({}));
                        break;
                    }
                    Err(e) => {
                        log::error!("sidecar: error reading stdout: {}", e);
                        break;
                    }
                }
            }
        });
    }

    // stderr is the sidecar's own log stream (human-readable), not protocol traffic.
    tokio::spawn(async move {
        let mut reader = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            log::info!("backend: {}", line);
        }
    });

    let ipc = IpcClient::new(stdin_tx, pending);
    Ok((ipc, child))
}
