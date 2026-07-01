# E-iClean — Technical Specification (v1.0)

**Status:** Draft for implementation
**Owner:** [your name]
**Target agent:** Coding agent (Claude Code or equivalent)
**Last updated:** 2026-06-30

---

## 0. Project Naming

**Chosen name: `E-iClean`**

Fits the existing product family naming convention (e-food, e-qr, e-trading, e-call). Communicates the product's two core jobs directly — iPhone ("i") + freeing up space ("Clean") — which matches the explicit decision to scope this product to iPhone only (no Android/multi-platform ambition for this line).

**Naming trade-off accepted knowingly:** the "i-" prefix sits close to Apple's own naming conventions (iPhone, iCloud, iMessage). This is **not** a trademark risk in itself (lots of third-party accessories and apps use "i-" prefixes for iPhone-related products), but worth a quick trademark/Apple-guideline sanity check before public launch — flagged as an open item in §9. The name also intentionally narrows scope to "cleanup," so if a future roadmap item moves this product toward general file management beyond iPhone storage cleanup, a rename should be revisited at that point rather than stretching this name to cover it.

Alternatives considered and rejected:

| Name | Why rejected |
|---|---|
| `E-FileTrans` | More generic/extensible, but doesn't communicate the actual value prop (freeing up phone storage) as directly as `E-iClean` |
| `E-Offload` | Signals the job but less brandable, less obviously iPhone-specific |
| `E-CamRoll` | Too narrow even relative to `E-iClean` — boxes the product into photos only with no room for future iPhone storage categories (e.g., large attachments, app caches) |

Working name used throughout this document: **E-iClean**. Codename/package id suggestion: `com.yourcompany.eiclean`.

---

## 1. Problem Statement

Users with iPhones and Windows PCs who want to free up phone storage today have to choose between:

1. **iCloud** — recurring subscription cost, photos live in the cloud rather than locally.
2. **iTunes/Apple Devices app + Windows Explorer/Photos import** — frequently freezes or disconnects on large libraries (1,000+ photos/videos), and any interruption forces a full re-import with no resume.
3. **Third-party tools (iMazing, AnyTrans, CopyTrans)** — paid, general-purpose "iPhone managers" with cluttered feature sets, inconsistent Wi-Fi reliability, and no first-class "transfer → verify → delete" workflow built around safely freeing up phone storage.

**Core user need:** Reliably copy all photos/videos from an iPhone to a local folder on a Windows PC — without losing progress on connection drops — and safely delete the originals from the phone only after the local copy is confirmed intact. No cloud account, no subscription, no app installation required on the iPhone itself.

---

## 2. Goals / Non-Goals

### Goals (MVP)
- Copy all photos and videos from iPhone Camera Roll to a user-chosen local folder on Windows, over a USB cable.
- Survive connection drops/cable disconnects without losing transfer progress (resume, not restart).
- Verify each transferred file before allowing the user to delete it from the phone.
- Require zero installation on the iPhone itself beyond the standard "Trust This Computer" system prompt.
- Ship as a single Windows installer with all required Apple driver dependencies bundled or auto-installed.

### Non-Goals (MVP — explicitly deferred to roadmap)
- Wi-Fi / wireless transfer (USB cable only for v1).
- Duplicate detection, burst-photo grouping, screenshot/blur detection, or any "smart cleanup" logic.
- Backup of non-photo data (contacts, messages, app data, etc.).
- macOS support (Windows-only for v1 — macOS already has Finder/Image Capture as adequate alternatives).
- Multi-device management / multiple iPhones in one session.
- Cloud sync of any kind.

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────┐
│  Desktop UI (Tauri shell, Rust + WebView)  │
│  - Connection status                        │
│  - Transfer queue / progress view           │
│  - Verify & Delete screen                   │
└───────────────────┬──────────────────────┘
                     │ local IPC (stdio/socket)
┌───────────────────▼──────────────────────┐
│  Backend sidecar process (Python)          │
│  - pymobiledevice3 (device comms)          │
│  - Transfer engine (queue, resume logic)   │
│  - SQLite (transfer state)                 │
│  - Verification (checksum/size compare)    │
└───────────────────┬──────────────────────┘
                     │ usbmux / lockdownd / AFC
┌───────────────────▼──────────────────────┐
│  iPhone (stock iOS, no app installed)      │
│  - Trust This Computer (one-time prompt)   │
│  - AFC service exposes /DCIM                │
└────────────────────────────────────────────┘
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| **UI shell (Tauri)** | All user-facing screens, progress visualization, settings, delete confirmation flows. No device logic lives here. |
| **Backend sidecar (Python + pymobiledevice3)** | Device discovery, pairing/trust handling, AFC file listing/read/write/delete, transfer queue execution, checksum verification, persistence of transfer state. |
| **SQLite DB (local, per-device)** | Source of truth for "what has been copied, what has been verified, what is safe to delete." Survives app restarts and connection drops. |
| **iPhone** | No custom software. Relies entirely on stock iOS services (lockdownd + AFC) that Apple ships for iTunes/Finder compatibility. |

---

## 4. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| UI shell | **Tauri** (Rust core + system WebView, HTML/CSS/JS or a lightweight framework for the view layer) | Smaller footprint and lower memory use than Electron — important since the product's whole pitch is "doesn't bog down your laptop while cleaning your phone." |
| Device communication | **pymobiledevice3** (Python) | Actively maintained, pure-Python, explicitly cross-platform including Windows. Implements usbmux, lockdownd pairing, and AFC file access without needing the full iTunes install. |
| Windows USB/driver dependency | **Apple Mobile Device Support** (Apple's own small USB driver component) | Required for `usbmuxd` to function correctly on Windows per upstream libimobiledevice documentation. Must be bundled or silently installed by our installer — see §5.8. |
| Backend process model | Python sidecar process spawned and managed by the Tauri Rust core, communicating over local stdio/socket IPC (e.g., JSON-RPC over a local pipe) | Keeps the device-comms layer in Python (where pymobiledevice3 lives) while keeping the UI layer lightweight and native. |
| Local state store | **SQLite** (single file per app install, e.g. `%APPDATA%/EiClean/state.db`) | Simple, embedded, no server, transactional — fits the "fully local" privacy promise. |
| Installer | **NSIS** or **WiX** for Windows installer, bundling the Python runtime (via PyInstaller/Nuitka) so end users never need Python installed separately | Avoids burdening non-technical users with manual dependency installation. |

---

## 5. MVP Scope — USB Copy with Resume

This is the only feature set in scope for v1. Everything else is roadmap (§8).

### 5.1 User Flow

1. User launches E-iClean on Windows.
2. User connects iPhone via USB cable.
3. App detects the device via `usbmux`. If not yet paired, app prompts user to unlock the phone and tap "Trust This Computer" — app polls until pairing succeeds or times out (60s default, configurable).
4. App establishes an AFC session and enumerates all photos/videos under `/DCIM`, building (or resuming) a transfer manifest in SQLite: file path, size, modification date, current status.
5. User selects/confirms a destination folder on the local disk (remembers last choice).
6. User clicks **Start Transfer**.
7. App copies files one at a time (or with a small configurable concurrency, default 1 — AFC is a single logical channel per session, so true parallelism is limited; see §5.4), updating progress per-file and overall.
8. **If the connection drops mid-transfer:** app detects the failure, marks the in-flight file as `partial` with its last confirmed byte offset, surfaces a "Connection lost — reconnect your iPhone" banner, and automatically resumes from where it left off once the device reconnects — no user action beyond physically reconnecting the cable.
9. Once all files reach `copied` status, app automatically verifies each one (§5.6) and promotes verified files to `verified`.
10. User reviews a summary screen: "X photos and Y videos copied and verified (Z GB)." User can optionally proceed to **Free Up Space**, which deletes only `verified` files from the phone, in confirmed batches.

### 5.2 Functional Requirements

| ID | Requirement |
|---|---|
| FR-1 | App MUST detect iPhone connection/disconnection events in real time without requiring manual "refresh." |
| FR-2 | App MUST NOT require any software installation on the iPhone beyond the OS-level Trust prompt. |
| FR-3 | App MUST enumerate all items in the Camera Roll (photos and videos, including HEIC/HEVC and Live Photo pairs) before starting transfer. |
| FR-4 | App MUST persist transfer progress to local disk such that an app restart or OS reboot does not lose progress. |
| FR-5 | App MUST resume an interrupted file transfer from the last confirmed byte offset rather than re-copying the full file, where the underlying AFC read API supports seek (see §5.4 for fallback behavior if not supported for a given file/session). |
| FR-6 | App MUST verify file integrity (§5.6) before exposing any "delete from phone" action for that file. |
| FR-7 | App MUST NOT delete any file from the phone without explicit user confirmation of a batch delete action. |
| FR-8 | App MUST handle Live Photos as a linked pair (HEIC/JPG + MOV) — both halves must reach `verified` before the pair is eligible for deletion, and deleting one without the other must not be possible from the UI. |
| FR-9 | App MUST surface clear, non-technical error messages for common failure modes (cable unplugged, phone locked, phone storage permission not granted, disk full on PC). |
| FR-10 | App MUST allow the user to cancel/pause a transfer and resume it later without data loss. |
| FR-11 | App MUST skip files already present and verified at the destination (idempotent re-runs — e.g., user runs the tool again a week later, only new photos should transfer). |

### 5.3 Data Model (SQLite)

```sql
CREATE TABLE devices (
    udid TEXT PRIMARY KEY,
    display_name TEXT,
    last_connected_at TIMESTAMP
);

CREATE TABLE transfer_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_udid TEXT NOT NULL REFERENCES devices(udid),
    remote_path TEXT NOT NULL,          -- e.g. /DCIM/100APPLE/IMG_0001.HEIC
    file_name TEXT NOT NULL,
    remote_size_bytes INTEGER NOT NULL,
    remote_modified_at TIMESTAMP,
    local_path TEXT,                    -- populated once destination is known
    bytes_transferred INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | in_progress | partial | copied | verified | delete_pending | deleted | failed
    checksum_local TEXT,                -- sha256, populated after verification
    live_photo_pair_id INTEGER REFERENCES transfer_items(id), -- NULL if not a Live Photo pair
    error_message TEXT,
    last_attempt_at TIMESTAMP,
    UNIQUE(device_udid, remote_path)
);

CREATE TABLE transfer_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_udid TEXT NOT NULL REFERENCES devices(udid),
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    total_files INTEGER,
    total_bytes INTEGER,
    outcome TEXT -- completed | interrupted | cancelled
);
```

### 5.4 Transfer Engine Design

- **Queue-based, single worker by default.** AFC is a stateful, single-channel protocol per device session; running multiple simultaneous file reads against one session is unreliable in practice. Default to sequential transfer; treat concurrency > 1 as an experimental setting, not a default.
- **Chunked reads.** Read each remote file in fixed-size chunks (e.g., 4 MB) via AFC, writing to a local `.partial` file. Update `bytes_transferred` in SQLite after every chunk (or every N chunks, e.g. every 5, to limit DB write overhead) so a crash mid-file loses at most one chunk's worth of progress, not the whole file.
- **Resume semantics:**
  - If a `.partial` file exists locally and its size matches `bytes_transferred` in the DB, resume by seeking to that offset on the remote file handle and continuing the chunk loop.
  - If the AFC session does not support seeking for a given read handle (verify this empirically against `pymobiledevice3`'s AFC client — some implementations require re-opening the handle and seeking via `lseek`-equivalent calls), fall back to discarding the partial local file and restarting that single file only — never the whole batch. This fallback should be rare and limited to one file at a time, not the entire queue.
- **Reconnection handling:**
  - Backend maintains a connection-watcher loop polling `usbmux` device list every 1–2 seconds.
  - On disconnect mid-transfer: mark current file `partial`, pause the queue, emit a `device_disconnected` event to the UI.
  - On reconnect (same UDID): re-establish AFC session, re-validate pairing/trust, resume the queue automatically from the first `partial`/`pending` item.
  - If a *different* device connects while a session is paused, surface a clear UI warning rather than silently mixing transfer state across devices.
- **Windows USB power management mitigation:** on transfer start, programmatically disable USB selective suspend for the active session (via Windows Power Management APIs / `powercfg` equivalent), and restore the previous setting on app exit. This directly targets one of the most common real-world causes of "random" disconnects during large transfers.

### 5.5 Connection & Reconnection Handling — UI States

| State | UI representation |
|---|---|
| `disconnected` | "Connect your iPhone with a USB cable to begin." |
| `awaiting_trust` | "Unlock your iPhone and tap **Trust** to continue." (with retry/timeout handling) |
| `enumerating` | "Scanning your photo library…" with indeterminate progress |
| `ready` | Shows item count, total size, destination folder picker, Start button |
| `transferring` | Per-file progress + overall progress bar, current file name, transfer speed, ETA |
| `connection_lost` | Banner: "Connection lost. Reconnect your iPhone to resume — no progress was lost." Queue paused, not cancelled. |
| `verifying` | "Verifying transferred files…" progress |
| `ready_to_clean` | Summary + "Free Up Space" CTA, with count/size of verified, deletable files |
| `deleting` | Progress of batch delete operation |
| `done` | Final summary: files copied, space freed, destination folder link |

### 5.6 Verification & Delete Workflow

1. After a file reaches `copied` (full byte count matches remote size), compute a local checksum (SHA-256) of the written file.
2. Where feasible, cross-check the remote file size reported by AFC against the final local file size as a first-pass integrity signal (cheap, catches truncated transfers immediately).
3. Mark the item `verified` only after both the size check and local checksum computation succeed without I/O errors.
4. **Delete is always a separate, explicit user action** — never automatic, even after verification. The "Free Up Space" screen must show exactly which files/how much space will be freed, and require a confirmation click.
5. Deletion is performed via AFC `remove` calls in small batches (e.g., 50 files at a time) with progress feedback, not a single blocking call for thousands of files.
6. Live Photo pairs (FR-8): both the image and motion component must be `verified` before either becomes eligible for the delete batch; they are deleted together or not at all.
7. After deletion, update `status` to `deleted` and log the transfer session outcome — never silently fail; if a delete call errors for a given file, leave it as `verified` (not `deleted`) and surface it as a retryable error rather than assuming success.

### 5.7 UI/UX Requirements (MVP screens)

1. **Home / Connect screen** — connection state, basic device info (name, model, free space if obtainable via lockdownd device info).
2. **Library scan / summary screen** — total items, total size, last sync date if a previous session exists for this device.
3. **Destination picker** — folder browser, remembers last-used path, warns if insufficient local disk space is detected relative to library size.
4. **Transfer progress screen** — overall + current file progress, speed, ETA, pause button, live "connection lost / resumed" status.
5. **Verification progress screen** — can be merged visually with transfer screen as a second progress phase.
6. **Free Up Space confirmation screen** — explicit list/summary of what will be deleted, with a clear count and size, and a confirm/cancel action.
7. **Completion / summary screen** — results, link to open the destination folder in Explorer.
8. **Settings (minimal for MVP)** — destination folder default, concurrency toggle (advanced/hidden by default), log file location.

### 5.8 Installer & Dependency Bundling

- Ship a single Windows installer (NSIS or WiX).
- Bundle the Python runtime and all dependencies (pymobiledevice3 and its sub-dependencies) using PyInstaller or Nuitka so end users never see a Python environment.
- Detect at install time (or first run) whether **Apple Mobile Device Support** is present (check for the relevant Windows service/driver). If absent, either:
  - (a) bundle and silently install Apple's redistributable driver component, or
  - (b) prompt the user with a one-click "Install Apple device driver" step and link to Apple's official redistributable.
  - Decision between (a) and (b) should be made during implementation based on Apple's redistribution terms at build time — flag this as an explicit task for the agent to verify before shipping, not to assume.
- App must clearly detect and report the "Apple Mobile Device Support not found" state in the UI rather than failing silently or crashing.

### 5.9 Error States & Logging

- All backend errors logged to a local rotating log file (`%APPDATA%/EiClean/logs/`), never transmitted anywhere (fully local product — no telemetry in MVP).
- User-facing error copy must avoid raw protocol/exception text; map known error classes (pairing failure, AFC timeout, disk full, permission denied) to plain-language messages with a suggested next step.
- Provide a "Copy diagnostic info" button in Settings for support purposes (local log excerpt, no personal file paths/content beyond file names if possible).

### 5.10 Acceptance Criteria for MVP Release

- [ ] Full Camera Roll (tested with at least 5,000 mixed photo/video items, including several GB of 4K video) transfers successfully end-to-end via USB.
- [ ] Physically unplugging the cable mid-transfer and reconnecting within 5 minutes resumes without re-copying already-confirmed bytes.
- [ ] Killing the app process mid-transfer and relaunching resumes correctly from persisted SQLite state.
- [ ] No file is ever offered for deletion before its `verified` status is set.
- [ ] Deleting a batch of verified files actually frees the reported space on the device (spot-checked against device storage settings).
- [ ] Live Photo pairs are never split during delete.
- [ ] Running the app a second time against the same (now partially-cleaned) library only transfers new items since the last run.
- [ ] Works with zero manual driver installation steps on a clean Windows 11 machine that has never had iTunes installed.

---

## 6. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Performance | Sustain at least USB 2.0-equivalent throughput (~30 MB/s) for large file transfers without UI freezing; UI must remain responsive during transfer (backend work off the UI thread). |
| Reliability | No data loss on disconnect, crash, or forced quit — verified via the acceptance criteria above. |
| Resource usage | Idle memory footprint under 150 MB; should not require GPU acceleration. |
| Portability | Windows 10 (21H2+) and Windows 11 supported. |
| Privacy | Fully local — no account, no telemetry, no network calls except local device communication, in MVP. |
| Accessibility | Standard OS-level accessibility (keyboard navigation, screen-reader-readable progress text) — not a deep requirement for MVP but should not actively break it. |

---

## 7. Security & Privacy

- No cloud component in MVP. No user data leaves the local machine.
- Device pairing/trust relies entirely on Apple's own lockdownd pairing mechanism — the app does not implement or weaken any of Apple's security model.
- Local SQLite DB and any cached file paths should be considered to contain sensitive metadata (file names, photo dates) — store under the user's own profile directory with standard OS file permissions, no special encryption required for MVP given there's no network exposure, but flag as a future hardening item if the roadmap ever adds cloud sync.
- Deletion from device must be irreversible-by-design from the app's perspective (no "trash" abstraction on the iOS side) — this makes the verify-before-delete requirement (§5.6) a hard security/trust requirement, not just a nice-to-have.

---

## 8. Roadmap (Post-MVP)

These are explicitly **out of scope for v1** and should not be implemented until the MVP acceptance criteria (§5.10) are met and validated with real users.

### 8.1 Wi-Fi Transfer
- Enable wireless lockdownd pairing after an initial USB connection (matches the pattern used by iMazing).
- Because the resume engine from MVP already tolerates interruptions, Wi-Fi's inherent flakiness becomes a UX inconvenience rather than a data-loss risk — this is the reason Wi-Fi is sequenced *after* the resume engine is proven, not before.
- Add a connection-quality indicator and an explicit recommendation to use USB for first-time large transfers.

### 8.2 Smart Cleanup Suite
- **Exact duplicate detection** — hash-based matching of already-transferred local library to find identical files (e.g., the same photo saved twice via different apps).
- **Burst photo grouping** — detect burst sequences (consecutive timestamps, similar EXIF) and suggest keeping only the best N of a burst.
- **Screenshot detection** — flag files matching iOS screenshot dimensions/metadata for an easy bulk-review/delete pass.
- **Blurry/low-quality photo detection** — lightweight on-device (laptop-side, fully local, no cloud ML API) blur scoring to flag candidates for review.
- **Large video flagging** — surface the largest video files first, since video is typically the dominant contributor to storage pressure.
- All smart-cleanup suggestions must be **review-and-confirm**, never auto-delete, consistent with the MVP's verify-before-delete philosophy.

### 8.3 Multi-Device / Multi-Library Support
- Support managing transfer history for multiple iPhones/iPads from one installation (e.g., a household with several devices), keyed by UDID in the existing `devices` table design.
- Support an explicit "merge" or "separate folder per device" destination strategy.

### 8.4 Other Candidate Features (unprioritized, for future consideration)
- Storage analytics dashboard (breakdown of what's taking space on the phone beyond just Camera Roll, e.g., apps, messages — would require additional AFC/lockdown services beyond MVP scope).
- Scheduled/automatic "run on connect" mode for recurring cleanups.
- Export of transfer history/reports.
- macOS port (lower priority — Finder/Image Capture already cover much of this need on Mac).
- Optional, explicitly opt-in encrypted local archive mode for users who want compressed/encrypted long-term storage rather than a flat folder of originals.

---

## 9. Open Questions / Risks (for the agent to flag back, not silently assume)

1. **iOS version behavior drift:** Apple has periodically changed AFC/lockdown behavior across iOS versions (e.g., additional permission prompts for photo library access in some versions). The agent should validate current behavior against the latest shipping iOS version at implementation time and flag any discrepancies from this spec rather than guessing.
2. **Apple Mobile Device Support redistribution:** confirm current licensing terms for bundling vs. prompting before finalizing the installer approach in §5.8.
3. **AFC seek/resume support:** confirm empirically (via `pymobiledevice3`) whether partial-file resume via seek is reliably supported across iOS versions/file types, or whether the safer default is "resume at the file level, restart at the byte level only when seek is confirmed safe."
4. **Live Photo detection reliability:** confirm the most robust method for pairing the image and `.MOV` components (filename convention vs. metadata) across iOS versions before relying on it for the delete-safety guarantee in FR-8.
5. **Large library enumeration performance:** validate AFC directory listing performance against very large libraries (20,000+ items) and design pagination/incremental enumeration if a full upfront listing proves slow.
6. **Naming/trademark check:** before public launch, do a basic trademark and app-store-naming-guideline check on "E-iClean" (and the "i-" prefix generally) — this is a quick legal/branding sanity check, not a development task, but should not be skipped silently.

---

## 10. References / Libraries

- pymobiledevice3 — https://github.com/doronz88/pymobiledevice3
- libimobiledevice project (reference protocol documentation) — https://libimobiledevice.org/
- Tauri — https://tauri.app/
- Apple Mobile Device Support — distributed as part of iTunes / the "Apple Devices" app on Microsoft Store; confirm current standalone redistributable availability at implementation time (see Open Question 2).

---

*End of specification. This document is intended as the working brief for an autonomous coding agent. The agent should treat §5 (MVP) as the immediate, fully-scoped implementation target, and §8 (Roadmap) as context only — not to be built until MVP acceptance criteria are met.*
