// Bootstrap + state machine wiring (spec §5.1 user flow, §5.5 UI states).
import { api, onEvent } from "./api.js";
import * as ui from "./ui.js";

const APP_VERSION = "0.1.0";
console.log(`E-iClean web UI v${APP_VERSION}`);

const state = {
  screen: "disconnected",
  udid: null,
  destination: null,
  totalItems: 0,
  totalBytes: 0,
  sessionId: null,
  transferredByItem: {},
  verifiedCount: 0,
  verifiedItemIds: [],
  deletedCount: 0,
  transferStartedAt: null,
};

let elapsedIntervalId = null;

function updateElapsedDisplay() {
  if (!state.transferStartedAt) return;
  const elapsedSeconds = (Date.now() - state.transferStartedAt.getTime()) / 1000;
  ui.renderTransferElapsed(elapsedSeconds);
}

function startElapsedTimer() {
  stopElapsedTimer();
  updateElapsedDisplay();
  elapsedIntervalId = setInterval(updateElapsedDisplay, 1000);
}

function stopElapsedTimer() {
  if (elapsedIntervalId) {
    clearInterval(elapsedIntervalId);
    elapsedIntervalId = null;
  }
}

function setScreen(screen) {
  const wasTransferring = state.screen === "transferring";
  state.screen = screen;
  ui.showScreen(screen);
  if (screen === "transferring") {
    startElapsedTimer();
  } else if (wasTransferring) {
    stopElapsedTimer();
  }
}

function backendErrorMessage(err) {
  if (err && typeof err === "object" && typeof err.message === "string") return err.message;
  if (typeof err === "string") return err;
  return "Something went wrong talking to your iPhone. Reconnect and try again.";
}

async function handleDeviceConnected({ udid, display_name }) {
  state.udid = udid;
  ui.renderDeviceInfo({ displayName: display_name, udid });
  ui.setConnectionLostBanner(false);
  ui.renderError(null);

  try {
    setScreen("awaiting_trust");
    const result = await api.deviceConnect(udid);
    if (result.status === "timed_out") {
      ui.renderError("Your iPhone didn't respond to the trust request in time. Reconnect and try again.");
      setScreen("disconnected");
      return;
    }

    setScreen("enumerating");
    const summary = await api.libraryEnumerate(udid);
    state.totalItems = summary.total_items;
    state.totalBytes = summary.total_bytes;
    ui.renderLibrarySummary({ totalItems: summary.total_items, totalBytes: summary.total_bytes });

    const settings = await api.settingsGet();
    state.destination = settings.values?.destination_default || state.destination;
    ui.renderDestination(state.destination);

    setScreen("ready");
  } catch (err) {
    // Without this, any backend error here (e.g. a dropped AFC connection mid-scan)
    // left the user stuck on "Scanning your photo library..." forever with no
    // feedback at all - see docs/DEVELOPMENT.md's known gaps.
    ui.renderError(backendErrorMessage(err));
    setScreen("disconnected");
  }
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

function handleDriverMissing() {
  ui.setDriverMissingBanner(true);
}

function handleDriverAvailable() {
  ui.setDriverMissingBanner(false);
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
}

function handleVerificationComplete({ verified_count, total_count, item_ids }) {
  // Authoritative completion signal - verification_progress only fires for items
  // actually (re-)verified this run, so it never fires at all when everything was
  // already verified from a prior run (see backend/app/ipc/handlers.py). item_ids is
  // likewise the authoritative currently-verified set, not just this run's delta.
  state.verifiedCount = verified_count;
  state.verifiedItemIds = item_ids;
  ui.renderVerificationProgress({ verifiedCount: verified_count, totalCount: total_count });
  setScreen("ready_to_clean");
  ui.renderReadyToClean({ verifiedCount: verified_count, freeableBytes: state.totalBytes });
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
  const chosen = await window.__TAURI__.dialog.open({
    directory: true,
    multiple: false,
    defaultPath: state.destination || undefined,
    title: "Choose destination folder",
  });
  if (chosen) {
    state.destination = chosen;
    ui.renderDestination(chosen);
    await api.settingsSet({ destination_default: chosen });
  }
}

async function onStartTransfer() {
  if (!state.udid || !state.destination) return;
  state.transferStartedAt = new Date();
  ui.renderTransferStart(state.transferStartedAt);
  setScreen("transferring");
  const result = await api.transferStart(state.udid, state.destination);
  state.sessionId = result.session_id;
}

async function onPauseTransfer() {
  if (state.sessionId) await api.transferPause(state.sessionId);
}

async function onFreeUpSpace() {
  const itemIds = state.verifiedItemIds || [];
  setScreen("deleting");
  await api.deleteBatch(itemIds);
}

function onOpenDestination() {
  // Known gap: opening Explorer needs the Tauri opener plugin's revealItemInDir, not
  // yet wired here (openUrl below only covers the driver-download links).
  window.alert(`Open this folder in Explorer:\n${state.destination}`);
}

function onExternalLinkClick(event) {
  const link = event.target.closest("a.external-link");
  if (!link) return;
  event.preventDefault();
  window.__TAURI__.opener.openUrl(link.href);
}

function wireButtons() {
  document.getElementById("choose-destination-btn")?.addEventListener("click", onChooseDestination);
  document.getElementById("start-transfer-btn")?.addEventListener("click", onStartTransfer);
  document.getElementById("pause-transfer-btn")?.addEventListener("click", onPauseTransfer);
  document.getElementById("free-up-space-btn")?.addEventListener("click", onFreeUpSpace);
  document.getElementById("open-destination-btn")?.addEventListener("click", onOpenDestination);
  document.body.addEventListener("click", onExternalLinkClick);
}

function wireBackendEvents() {
  onEvent("device_connected", handleDeviceConnected);
  onEvent("device_disconnected", handleDeviceDisconnected);
  onEvent("connection_lost", handleConnectionLost);
  onEvent("connection_resumed", handleConnectionResumed);
  onEvent("driver_missing", handleDriverMissing);
  onEvent("driver_available", handleDriverAvailable);
  onEvent("transfer_progress", handleTransferProgress);
  onEvent("verification_progress", handleVerificationProgress);
  onEvent("verification_complete", handleVerificationComplete);
  onEvent("delete_progress", handleDeleteProgress);
  onEvent("backend_crashed", () =>
    ui.renderError("The backend process stopped unexpectedly. Restart E-iClean.")
  );
}

function init() {
  setScreen("disconnected");
  wireButtons();
  wireBackendEvents();
}

window.addEventListener("DOMContentLoaded", init);
