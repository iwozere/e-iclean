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
  // Library Cleanup module (spec §11) - fully independent flow, no udid involved.
  "library-idle",
  "library-scanning",
  "library-review",
  "library-deleting",
  "library-done",
];

export function showScreen(screenName) {
  for (const name of SCREENS) {
    const el = document.getElementById(`screen-${name}`);
    if (el) el.hidden = name !== screenName;
  }
}

export function setActiveMode(mode) {
  const transferTab = document.getElementById("mode-nav-transfer");
  const libraryTab = document.getElementById("mode-nav-library");
  const transferPane = document.getElementById("mode-transfer");
  const libraryPane = document.getElementById("mode-library");
  if (transferTab) transferTab.classList.toggle("active", mode === "transfer");
  if (libraryTab) libraryTab.classList.toggle("active", mode === "library");
  if (transferPane) transferPane.hidden = mode !== "transfer";
  if (libraryPane) libraryPane.hidden = mode !== "library";
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

export function formatDuration(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

export function renderTransferElapsed(totalSeconds) {
  setText("transfer-elapsed", formatDuration(totalSeconds));
}

export function renderCopyDuration(totalSeconds) {
  const el = document.getElementById("cleanup-duration");
  if (!el) return;
  if (totalSeconds === null || totalSeconds === undefined) {
    el.hidden = true;
    return;
  }
  el.textContent = `Copy took ${formatDuration(totalSeconds)} (hh:mm:ss).`;
  el.hidden = false;
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

export function renderCleanupFailedNote(failedCount) {
  const el = document.getElementById("cleanup-failed-note");
  if (!el) return;
  if (failedCount > 0) {
    el.textContent = `${failedCount} file${failedCount === 1 ? "" : "s"} could not be copied or verified and will not be deleted from your iPhone. Reconnect and click Start Transfer again to retry ${failedCount === 1 ? "it" : "them"}.`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
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

export function renderLibraryScanProgress({ scanned, total }) {
  const pct = total ? Math.round((scanned / total) * 100) : 0;
  setWidth("library-scan-progress-bar", pct);
  setText("library-scan-progress-label", `${scanned} / ${total} files`);
}

// Rebuilds the duplicate-groups review list from scratch on every call - simplest
// correct approach given groups can be added/removed/pruned by deletions (spec
// §11.5's group-pruning) between renders; this module targets thousands, not
// hundreds of thousands, of files, so a full DOM rebuild per scan/refresh is not a
// meaningful cost. Nothing is pre-checked (spec FR-L3) - every checkbox starts
// unchecked regardless of group type.
export function renderDuplicateGroups(groups) {
  const container = document.getElementById("library-groups-container");
  const emptyNote = document.getElementById("library-groups-empty-note");
  if (!container) return;
  container.innerHTML = "";

  if (emptyNote) emptyNote.hidden = groups.length > 0;
  if (groups.length === 0) return;

  for (const group of groups) {
    const groupEl = document.createElement("div");
    groupEl.className = "dup-group";
    groupEl.dataset.groupId = group.id;

    const heading = document.createElement("div");
    heading.className = "dup-group-heading";
    heading.textContent =
      group.group_type === "exact"
        ? `Exact duplicates — ${group.members.length} copies`
        : `Similar photos — ${group.members.length} copies`;
    groupEl.appendChild(heading);

    const membersEl = document.createElement("div");
    membersEl.className = "dup-group-members";
    for (const member of group.members) {
      membersEl.appendChild(_buildMemberCard(member));
    }
    groupEl.appendChild(membersEl);
    container.appendChild(groupEl);
  }
}

function _buildMemberCard(member) {
  const card = document.createElement("label");
  card.className = "dup-member";

  const img = document.createElement("img");
  img.className = "dup-member-thumb";
  img.loading = "lazy";
  img.alt = member.local_path;
  img.src = window.__TAURI__.core.convertFileSrc(member.local_path);
  card.appendChild(img);

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "dup-member-checkbox";
  checkbox.dataset.fileId = String(member.id);
  checkbox.dataset.sizeBytes = String(member.size_bytes);
  card.appendChild(checkbox);

  const meta = document.createElement("div");
  meta.className = "dup-member-meta";
  const pathEl = document.createElement("span");
  pathEl.className = "dup-member-path";
  pathEl.title = member.local_path;
  pathEl.textContent = member.local_path;
  const sizeEl = document.createElement("span");
  sizeEl.textContent = formatBytes(member.size_bytes);
  meta.appendChild(pathEl);
  meta.appendChild(sizeEl);
  card.appendChild(meta);

  return card;
}

// Opens the comparison modal by *relocating* the group's real .dup-member nodes
// (checkbox and all) into the modal, rather than building copies - the small grid
// thumbnails were hard to actually compare (user feedback), but rebuilding fresh
// nodes in the modal would need a second, separate mechanism to keep their checked
// state in sync with the grid. Moving the same nodes sidesteps that entirely: it's
// the same <input>, so nothing can ever drift out of sync, and the existing
// document-level `change` delegation (see main.js) keeps working unchanged
// regardless of which container currently holds it.
export function openCompareModal(groupId) {
  const groupEl = document.querySelector(`.dup-group[data-group-id="${groupId}"]`);
  const modal = document.getElementById("library-compare-modal");
  const modalImages = document.getElementById("library-compare-images");
  if (!groupEl || !modal || !modalImages) return;
  const membersEl = groupEl.querySelector(".dup-group-members");
  if (!membersEl) return;

  modalImages.dataset.returnToGroupId = groupId;
  while (membersEl.firstChild) {
    modalImages.appendChild(membersEl.firstChild);
  }
  modal.hidden = false;
}

export function closeCompareModal() {
  const modal = document.getElementById("library-compare-modal");
  const modalImages = document.getElementById("library-compare-images");
  if (!modal || !modalImages) return;
  const groupId = modalImages.dataset.returnToGroupId;
  const groupEl = groupId && document.querySelector(`.dup-group[data-group-id="${groupId}"]`);
  const membersEl = groupEl && groupEl.querySelector(".dup-group-members");
  if (membersEl) {
    while (modalImages.firstChild) {
      membersEl.appendChild(modalImages.firstChild);
    }
  }
  modal.hidden = true;
}

export function renderLibrarySelection({ count, sizeBytes }) {
  setText("library-selected-count", count);
  setText("library-selected-size", formatBytes(sizeBytes));
  const btn = document.getElementById("library-delete-selected-btn");
  if (btn) btn.disabled = count === 0;
}

export function renderLibraryDoneSummary({ count, freedBytes, permanent }) {
  const verb = permanent ? "permanently deleted" : "moved to the delete folder";
  setText(
    "library-done-summary",
    `${count} file${count === 1 ? "" : "s"} ${verb} (${formatBytes(freedBytes)}).`
  );
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
