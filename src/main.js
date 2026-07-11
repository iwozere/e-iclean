// Bootstrap + state machine wiring (spec §5.1 user flow, §5.5 UI states).
import { api, onEvent } from "./api.js";
import * as ui from "./ui.js";

const APP_VERSION = "0.1.9";
console.log(`E-iClean web UI v${APP_VERSION}`);

const state = {
  screen: "disconnected",
  udid: null,
  destination: null,
  totalItems: 0,
  totalBytes: 0,
  sessionId: null,
  verifiedCount: 0,
  verifiedItemIds: [],
  freeableBytes: 0,
  deletedCount: 0,
  transferStartedAt: null,
  // Per-mode "last screen" so switching the top nav tab and back doesn't lose your
  // place in either flow (see setScreen/switchMode below).
  mode: "transfer",
  transferScreen: "disconnected",
  libraryScreen: "library-idle",
  // Library Cleanup module (spec §11) - fully independent of everything above, no
  // udid anywhere in this block.
  libraryRoot: null,
  librarySelectedFileIds: new Set(),
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
  if (screen.startsWith("library-")) {
    state.libraryScreen = screen;
  } else {
    state.transferScreen = screen;
  }
  ui.showScreen(screen);
  if (screen === "transferring") {
    startElapsedTimer();
  } else if (wasTransferring) {
    stopElapsedTimer();
  }
}

function switchMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  ui.setActiveMode(mode);
  setScreen(mode === "library" ? state.libraryScreen : state.transferScreen);
}

function backendErrorMessage(err) {
  if (err && typeof err === "object" && typeof err.message === "string") return err.message;
  if (typeof err === "string") return err;
  return "Something went wrong talking to your iPhone. Reconnect and try again.";
}

async function refreshLibrary(udid) {
  setScreen("enumerating");
  // library.enumerate also re-validates local files against disk (see
  // backend/app/services/enumeration.py::requeue_missing_local_files) - the same
  // check runs whether this is the initial connect or an explicit Re-check Library.
  const summary = await api.libraryEnumerate(udid);
  // A successful enumerate call is itself proof the device is reachable again - clear
  // any stale "Connection lost" banner here so it also resolves via Re-check Library,
  // not just the initial device_connected path (which already cleared it separately).
  ui.setConnectionLostBanner(false);
  state.totalItems = summary.total_items;
  state.totalBytes = summary.total_bytes;
  ui.renderLibrarySummary({ totalItems: summary.total_items, totalBytes: summary.total_bytes });

  const settings = await api.settingsGet();
  state.destination = settings.values?.destination_default || state.destination;
  ui.renderDestination(state.destination);

  setScreen("ready");
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

    await refreshLibrary(udid);
  } catch (err) {
    // Without this, any backend error here (e.g. a dropped AFC connection mid-scan)
    // left the user stuck on "Scanning your photo library..." forever with no
    // feedback at all - see docs/DEVELOPMENT.md's known gaps.
    ui.renderError(backendErrorMessage(err));
    setScreen("disconnected");
  }
}

async function onRecheckLibrary() {
  // The only way back from Free Up Space/Done before this was disconnecting and
  // reconnecting the device - this button is the direct equivalent, without needing
  // the cable pulled, and it re-validates against disk (see refreshLibrary above).
  if (!state.udid) {
    ui.renderError("Your iPhone isn't connected. Reconnect it to check the library again.");
    setScreen("disconnected");
    return;
  }
  try {
    ui.renderError(null);
    await refreshLibrary(state.udid);
  } catch (err) {
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
  // device_bytes_transferred is an authoritative, DB-sourced total (see
  // backend/app/services/transfer_engine.py) - not reconstructed from this session's
  // own event stream, which used to reset to 0 on every app restart even though the
  // device/DB still remembered everything already copied in earlier sessions.
  ui.renderTransferProgress({
    currentFileName: `item ${payload.item_id}`,
    overallTransferred: payload.device_bytes_transferred,
    overallTotal: state.totalBytes,
  });
}

function handleVerificationProgress(payload) {
  if (payload.verified) state.verifiedCount += 1;
  ui.renderVerificationProgress({ verifiedCount: state.verifiedCount, totalCount: state.totalItems });
}

function handleVerificationComplete({ verified_count, total_count, item_ids, verified_bytes }) {
  // Authoritative completion signal - verification_progress only fires for items
  // actually (re-)verified this run, so it never fires at all when everything was
  // already verified from a prior run (see backend/app/ipc/handlers.py). item_ids is
  // likewise the authoritative currently-verified set, not just this run's delta.
  state.verifiedCount = verified_count;
  state.verifiedItemIds = item_ids;
  // verified_bytes is the size of only the verified items - state.totalBytes is the
  // whole device library as of the last enumerate (see refreshLibrary), which is
  // wrong here whenever some items failed: it previously produced messages like
  // "0 verified files - 6.3 GB can be freed" because the stale library-wide total was
  // shown regardless of how many items actually verified.
  state.freeableBytes = verified_bytes;
  ui.renderVerificationProgress({ verifiedCount: verified_count, totalCount: total_count });
  setScreen("ready_to_clean");
  ui.renderReadyToClean({ verifiedCount: verified_count, freeableBytes: verified_bytes });
  // total_count here is every item for this device, not just this run's queue - by
  // this point (queue drained + verify pass done) anything not verified is failed,
  // not still pending. Surfacing this - previously silent - is what would have told
  // the user their item count didn't match instead of them having to notice on their
  // own (see docs/DEVELOPMENT.md's filename-collision bug for the real case this hit).
  ui.renderCleanupFailedNote(total_count - verified_count);
  // transferStartedAt is only set by this session's own Start Transfer click (see
  // onStartTransfer) - e.g. arriving here via Re-check Library on an already-fully-
  // verified device has no meaningful duration to show, so hide it rather than
  // report a stale or fabricated number.
  const elapsedSeconds = state.transferStartedAt ? (Date.now() - state.transferStartedAt.getTime()) / 1000 : null;
  ui.renderCopyDuration(elapsedSeconds);
}

function handleDeleteProgress(payload) {
  if (payload.deleted) state.deletedCount += 1;
  ui.renderDeleteProgress({ deletedCount: state.deletedCount, totalCount: state.verifiedCount });
  if (state.deletedCount >= state.verifiedCount && state.verifiedCount > 0) {
    setScreen("done");
    ui.renderDoneSummary({
      // Same fix as handleVerificationComplete above: state.totalItems/totalBytes are
      // the whole device library, not what actually got verified and deleted this
      // round - using them here overstated the done summary whenever some items had
      // failed (e.g. claiming the full library size was freed when only a subset was).
      copiedCount: state.verifiedCount,
      freedBytes: state.freeableBytes,
      destination: state.destination,
    });
  }
}

// --- Library Cleanup module (spec §11) - fully independent of the iPhone-transfer
// handlers above; no udid anywhere below. ---

function handleLibraryScanProgress(payload) {
  ui.renderLibraryScanProgress({ scanned: payload.scanned, total: payload.total });
}

async function handleLibraryScanComplete() {
  // Without this try/catch, any failure here (e.g. rendering thousands of duplicate-
  // group thumbnails) becomes an unhandled promise rejection - Tauri's event listener
  // doesn't await this callback, so the error is invisible and the UI is stuck on
  // "Scanning..." forever even though the backend finished successfully. Confirmed
  // live: a real ~1264-file scan completed and formed 27 groups in the DB, but the
  // screen never advanced.
  try {
    await refreshLibraryGroups();
    setScreen("library-review");
  } catch (err) {
    ui.renderError(backendErrorMessage(err));
    setScreen("library-idle");
  }
}

async function refreshLibraryGroups() {
  const result = await api.libraryScanGroups();
  state.librarySelectedFileIds = new Set();
  ui.renderDuplicateGroups(result.groups);
  ui.renderLibrarySelection({ count: 0, sizeBytes: 0 });
}

async function startLibraryScan(root) {
  state.libraryRoot = root;
  ui.renderError(null);
  try {
    ui.renderLibraryScanProgress({ scanned: 0, total: 0 });
    setScreen("library-scanning");
    await api.libraryScanStart(root);
  } catch (err) {
    ui.renderError(backendErrorMessage(err));
    setScreen("library-idle");
  }
}

async function onLibraryChooseFolder() {
  const chosen = await window.__TAURI__.dialog.open({
    directory: true,
    multiple: false,
    title: "Choose a folder to scan for duplicates",
  });
  if (!chosen) return;
  await startLibraryScan(chosen);
}

function onLibraryGroupsChange(event) {
  const checkbox = event.target.closest(".dup-member-checkbox");
  if (!checkbox) return;
  const fileId = Number(checkbox.dataset.fileId);
  if (checkbox.checked) {
    state.librarySelectedFileIds.add(fileId);
  } else {
    state.librarySelectedFileIds.delete(fileId);
  }
  recomputeLibrarySelection();
}

function onLibraryThumbnailClick(event) {
  const img = event.target.closest(".dup-member-thumb");
  if (!img) return;
  // The thumbnail sits inside a <label> alongside its checkbox (see
  // ui.js::_buildMemberCard) so clicking the image would otherwise also toggle
  // selection via the browser's default label-forwards-click-to-control behavior -
  // suppress that here so "view larger" and "select for deletion" stay independent
  // actions, since they're both driven by clicking somewhere on the same card.
  event.preventDefault();
  const groupEl = img.closest(".dup-group");
  if (!groupEl) return;
  ui.openCompareModal(groupEl.dataset.groupId);
}

function onLibraryCompareModalBackdropClick(event) {
  if (event.target.id === "library-compare-modal") ui.closeCompareModal();
}

function onKeyDown(event) {
  if (event.key === "Escape") ui.closeCompareModal();
}

function recomputeLibrarySelection() {
  let sizeBytes = 0;
  for (const checkbox of document.querySelectorAll(".dup-member-checkbox")) {
    if (state.librarySelectedFileIds.has(Number(checkbox.dataset.fileId))) {
      sizeBytes += Number(checkbox.dataset.sizeBytes);
    }
  }
  ui.renderLibrarySelection({ count: state.librarySelectedFileIds.size, sizeBytes });
}

async function onLibraryDeleteSelected() {
  const ids = Array.from(state.librarySelectedFileIds);
  if (ids.length === 0) return;
  // Checked (the default) means safe move-to-<root>-delete-folder; unchecked is the
  // explicit opt-in to permanent delete (spec FR-L7).
  const safeDeleteChecked = document.getElementById("library-safe-delete-checkbox")?.checked ?? true;
  const permanent = !safeDeleteChecked;

  let selectedSizeBytes = 0;
  for (const checkbox of document.querySelectorAll(".dup-member-checkbox")) {
    if (state.librarySelectedFileIds.has(Number(checkbox.dataset.fileId))) {
      selectedSizeBytes += Number(checkbox.dataset.sizeBytes);
    }
  }

  setScreen("library-deleting");
  try {
    const result = await api.libraryDeleteBatch(ids, permanent);
    ui.renderLibraryDoneSummary({ count: result.deleted_count, freedBytes: selectedSizeBytes, permanent });
    setScreen("library-done");
  } catch (err) {
    ui.renderError(backendErrorMessage(err));
    setScreen("library-review");
  }
}

async function onLibraryReviewAgain() {
  await refreshLibraryGroups();
  setScreen("library-review");
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
  document.getElementById("recheck-library-btn-ready")?.addEventListener("click", onRecheckLibrary);
  document.getElementById("recheck-library-btn-done")?.addEventListener("click", onRecheckLibrary);
  document.body.addEventListener("click", onExternalLinkClick);

  document.getElementById("mode-nav-transfer")?.addEventListener("click", () => switchMode("transfer"));
  document.getElementById("mode-nav-library")?.addEventListener("click", () => switchMode("library"));
  document.getElementById("library-choose-folder-btn")?.addEventListener("click", onLibraryChooseFolder);
  document.getElementById("library-rescan-btn")?.addEventListener("click", onLibraryChooseFolder);
  document.getElementById("library-scan-another-btn")?.addEventListener("click", onLibraryChooseFolder);
  document.getElementById("library-review-again-btn")?.addEventListener("click", onLibraryReviewAgain);
  document.getElementById("library-delete-selected-btn")?.addEventListener("click", onLibraryDeleteSelected);
  document.getElementById("library-compare-close-btn")?.addEventListener("click", ui.closeCompareModal);
  document.getElementById("library-compare-modal")?.addEventListener("click", onLibraryCompareModalBackdropClick);
  // Delegated on document.body, not #library-groups-container: openCompareModal
  // (ui.js) relocates a group's real member nodes into the modal on click, which
  // lives outside that container - a narrower listener would stop catching
  // checkbox/thumbnail interactions the moment the modal opens.
  document.body.addEventListener("change", onLibraryGroupsChange);
  document.body.addEventListener("click", onLibraryThumbnailClick);
  document.addEventListener("keydown", onKeyDown);
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
  onEvent("library_scan_progress", handleLibraryScanProgress);
  onEvent("library_scan_complete", handleLibraryScanComplete);
  onEvent("backend_crashed", () =>
    ui.renderError("The backend process stopped unexpectedly. Restart E-iClean.")
  );
}

function init() {
  setScreen("disconnected");
  ui.setActiveMode("transfer");
  wireButtons();
  wireBackendEvents();
}

window.addEventListener("DOMContentLoaded", init);
