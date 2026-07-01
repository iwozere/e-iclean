// DOM rendering for the MVP screens (spec docs/project_specification.md §5.7).
// One state machine, driven entirely by explicit backend events - see AGENTS.md §11.

const SCREENS = [
  "disconnected",
  "awaiting_trust",
  "enumerating",
  "ready",
  "transferring",
  "verifying",
  "ready_to_clean",
  "deleting",
  "done",
];

export function showScreen(screenName) {
  for (const name of SCREENS) {
    const el = document.getElementById(`screen-${name}`);
    if (el) el.hidden = name !== screenName;
  }
}

export function setConnectionLostBanner(visible) {
  const banner = document.getElementById("connection-lost-banner");
  if (banner) banner.hidden = !visible;
}

export function setDriverMissingBanner(visible) {
  const banner = document.getElementById("driver-missing-banner");
  if (banner) banner.hidden = !visible;
}

export function renderDeviceInfo({ displayName, udid }) {
  const el = document.getElementById("device-name");
  if (el) el.textContent = displayName || udid || "";
}

export function renderLibrarySummary({ totalItems, totalBytes }) {
  setText("library-item-count", totalItems);
  setText("library-size", formatBytes(totalBytes));
}

export function renderDestination(path) {
  setText("destination-path", path || "(not selected)");
}

export function renderTransferStart(date) {
  // No explicit locale/options - lets the OS/system locale decide the format,
  // matching whatever the user's Windows regional settings already use.
  setText("transfer-start-time", date.toLocaleString());
}

export function renderTransferElapsed(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const pad = (n) => String(n).padStart(2, "0");
  setText("transfer-elapsed", `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`);
}

export function renderTransferProgress({ currentFileName, overallTransferred, overallTotal }) {
  const pct = overallTotal ? Math.round((overallTransferred / overallTotal) * 100) : 0;
  setWidth("transfer-progress-bar", pct);
  setText("transfer-progress-label", `${pct}% - ${formatBytes(overallTransferred)} / ${formatBytes(overallTotal)}`);
  setText("transfer-current-file", currentFileName || "");
}

export function renderVerificationProgress({ verifiedCount, totalCount }) {
  const pct = totalCount ? Math.round((verifiedCount / totalCount) * 100) : 0;
  setWidth("verify-progress-bar", pct);
  setText("verify-progress-label", `${verifiedCount} / ${totalCount} verified`);
}

export function renderReadyToClean({ verifiedCount, freeableBytes }) {
  setText("cleanup-count", verifiedCount);
  setText("cleanup-size", formatBytes(freeableBytes));
}

export function renderDeleteProgress({ deletedCount, totalCount }) {
  const pct = totalCount ? Math.round((deletedCount / totalCount) * 100) : 0;
  setWidth("delete-progress-bar", pct);
  setText("delete-progress-label", `${deletedCount} / ${totalCount} deleted`);
}

export function renderDoneSummary({ copiedCount, freedBytes, destination }) {
  setText("done-summary", `${copiedCount} photos and videos copied and verified (${formatBytes(freedBytes)}).`);
  const link = document.getElementById("done-destination-link");
  if (link) {
    link.textContent = destination;
    link.dataset.path = destination;
  }
}

export function renderError(message) {
  const el = document.getElementById("error-banner");
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setWidth(id, percent) {
  const el = document.getElementById(id);
  if (el) el.style.width = `${percent}%`;
}
