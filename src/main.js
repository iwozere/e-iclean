// Bootstrap + state machine wiring (spec §5.1 user flow, §5.5 UI states).
import { api, onEvent } from "./api.js";
import * as ui from "./ui.js";

const APP_VERSION = "0.1.0";
console.log(`E-FileTrans web UI v${APP_VERSION}`);

const state = {
  screen: "disconnected",
  udid: null,
  destination: null,
  totalItems: 0,
  totalBytes: 0,
  sessionId: null,
  transferredByItem: {},
  verifiedCount: 0,
  deletedCount: 0,
};

function setScreen(screen) {
  state.screen = screen;
  ui.showScreen(screen);
}

async function handleDeviceConnected({ udid, display_name }) {
  state.udid = udid;
  ui.renderDeviceInfo({ displayName: display_name, udid });
  ui.setConnectionLostBanner(false);

  setScreen("awaiting_trust");
  const result = await api.deviceConnect(udid);
  if (result.status === "timed_out") {
    ui.renderError("Your iPhone didn't respond to the trust request in time. Reconnect and try again.");
    setScreen("disconnected");
    return;
  }
  ui.renderError(null);

  setScreen("enumerating");
  const summary = await api.libraryEnumerate(udid);
  state.totalItems = summary.total_items;
  state.totalBytes = summary.total_bytes;
  ui.renderLibrarySummary({ totalItems: summary.total_items, totalBytes: summary.total_bytes });

  const settings = await api.settingsGet();
  state.destination = settings.values?.destination_default || state.destination;
  ui.renderDestination(state.destination);

  setScreen("ready");
}

function handleDeviceDisconnected() {
  const midTransfer = state.screen === "transferring" || state.screen === "verifying";
  if (midTransfer) {
    ui.setConnectionLostBanner(true);
    return;
  }
  state.udid = null;
  setScreen("disconnected");
}

function handleConnectionLost() {
  ui.setConnectionLostBanner(true);
}

function handleConnectionResumed() {
  ui.setConnectionLostBanner(false);
}

function handleTransferProgress(payload) {
  state.transferredByItem[payload.item_id] = payload.bytes_transferred;
  const overallTransferred = Object.values(state.transferredByItem).reduce((sum, n) => sum + n, 0);
  ui.renderTransferProgress({
    currentFileName: `item ${payload.item_id}`,
    overallTransferred,
    overallTotal: state.totalBytes,
  });
}

function handleVerificationProgress(payload) {
  if (payload.verified) state.verifiedCount += 1;
  ui.renderVerificationProgress({ verifiedCount: state.verifiedCount, totalCount: state.totalItems });
  if (state.verifiedCount >= state.totalItems && state.totalItems > 0) {
    setScreen("ready_to_clean");
    ui.renderReadyToClean({ verifiedCount: state.verifiedCount, freeableBytes: state.totalBytes });
  }
}

function handleDeleteProgress(payload) {
  if (payload.deleted) state.deletedCount += 1;
  ui.renderDeleteProgress({ deletedCount: state.deletedCount, totalCount: state.verifiedCount });
  if (state.deletedCount >= state.verifiedCount && state.verifiedCount > 0) {
    setScreen("done");
    ui.renderDoneSummary({
      copiedCount: state.totalItems,
      freedBytes: state.totalBytes,
      destination: state.destination,
    });
  }
}

async function onChooseDestination() {
  // Known gap: the native folder-picker dialog needs the Tauri dialog plugin, not
  // yet wired - see README "Known gaps". A text prompt keeps the flow usable now.
  const chosen = window.prompt("Destination folder path:", state.destination || "C:\\Photos");
  if (chosen) {
    state.destination = chosen;
    ui.renderDestination(chosen);
    await api.settingsSet({ destination_default: chosen });
  }
}

async function onStartTransfer() {
  if (!state.udid || !state.destination) return;
  setScreen("transferring");
  const result = await api.transferStart(state.udid, state.destination);
  state.sessionId = result.session_id;
}

async function onPauseTransfer() {
  if (state.sessionId) await api.transferPause(state.sessionId);
}

async function onFreeUpSpace() {
  const itemIds = Object.keys(state.transferredByItem).map(Number);
  setScreen("deleting");
  await api.deleteBatch(itemIds);
}

function onOpenDestination() {
  // Known gap: opening Explorer needs the Tauri opener plugin, not yet wired.
  window.alert(`Open this folder in Explorer:\n${state.destination}`);
}

function wireButtons() {
  document.getElementById("choose-destination-btn")?.addEventListener("click", onChooseDestination);
  document.getElementById("start-transfer-btn")?.addEventListener("click", onStartTransfer);
  document.getElementById("pause-transfer-btn")?.addEventListener("click", onPauseTransfer);
  document.getElementById("free-up-space-btn")?.addEventListener("click", onFreeUpSpace);
  document.getElementById("open-destination-btn")?.addEventListener("click", onOpenDestination);
}

function wireBackendEvents() {
  onEvent("device_connected", handleDeviceConnected);
  onEvent("device_disconnected", handleDeviceDisconnected);
  onEvent("connection_lost", handleConnectionLost);
  onEvent("connection_resumed", handleConnectionResumed);
  onEvent("transfer_progress", handleTransferProgress);
  onEvent("verification_progress", handleVerificationProgress);
  onEvent("delete_progress", handleDeleteProgress);
  onEvent("backend_crashed", () =>
    ui.renderError("The backend process stopped unexpectedly. Restart E-FileTrans.")
  );
}

function init() {
  setScreen("disconnected");
  wireButtons();
  wireBackendEvents();
}

window.addEventListener("DOMContentLoaded", init);
