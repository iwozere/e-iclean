// Thin wrappers around the Tauri bridge command + event system. Never construct raw
// JSON-RPC here - the shape lives in docs/ipc_protocol.md and is owned by
// src-tauri/src/ipc.rs / backend/app/ipc/.

const tauriCore = window.__TAURI__.core;
const tauriEvent = window.__TAURI__.event;

async function backendCall(method, params = {}) {
  return tauriCore.invoke("backend_call", { method, params });
}

export function onEvent(eventName, handler) {
  return tauriEvent.listen(eventName, (event) => handler(event.payload));
}

export const api = {
  deviceList: () => backendCall("device.list"),
  deviceConnect: (udid) => backendCall("device.connect", { udid }),
  libraryEnumerate: (udid) => backendCall("library.enumerate", { udid }),
  transferStart: (udid, destination) => backendCall("transfer.start", { udid, destination }),
  transferPause: (sessionId) => backendCall("transfer.pause", { session_id: sessionId }),
  transferCancel: (sessionId) => backendCall("transfer.cancel", { session_id: sessionId }),
  verifyStatus: (sessionId) => backendCall("verify.status", { session_id: sessionId }),
  deleteBatch: (itemIds) => backendCall("delete.batch", { item_ids: itemIds }),
  settingsGet: () => backendCall("settings.get"),
  settingsSet: (values) => backendCall("settings.set", { values }),
};
