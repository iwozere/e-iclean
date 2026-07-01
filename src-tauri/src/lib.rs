mod commands;
mod events;
mod ipc;
mod sidecar;

use std::sync::Arc;

use tauri::Manager;
use tokio::process::Child;
use tokio::sync::Mutex as AsyncMutex;

/// Holds the sidecar's `Child` handle so it can be killed on app exit. Wrapped for
/// interior mutability since Tauri-managed state must be `Send + Sync + 'static`.
struct SidecarProcess(Arc<AsyncMutex<Option<Child>>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            let app_handle = app.handle().clone();
            let child_slot: Arc<AsyncMutex<Option<Child>>> = Arc::new(AsyncMutex::new(None));
            let child_slot_for_setup = child_slot.clone();

            tauri::async_runtime::block_on(async move {
                match sidecar::spawn_sidecar(app_handle.clone()).await {
                    Ok((ipc, child)) => {
                        app_handle.manage(ipc);
                        *child_slot_for_setup.lock().await = Some(child);
                    }
                    Err(e) => {
                        // The UI must see this as a real error state, not a silent
                        // hang (AGENTS.md §10) - device.list / any backend_call will
                        // fail fast since IpcClient was never managed.
                        log::error!("failed to start backend sidecar: {}", e);
                    }
                }
            });

            app.manage(SidecarProcess(child_slot));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![commands::backend_call])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(sidecar_process) = app_handle.try_state::<SidecarProcess>() {
                    let slot = sidecar_process.0.clone();
                    tauri::async_runtime::block_on(async move {
                        if let Some(mut child) = slot.lock().await.take() {
                            let _ = child.kill().await;
                        }
                    });
                }
            }
        });
}
