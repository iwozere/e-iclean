# E-iClean — Technical Specification (v1.0)

**Status:** Draft for implementation — MVP core loop (§5) validated end-to-end against
real hardware as of 2026-07-11; see `docs/DEVELOPMENT.md` for current status and §5.10
for the acceptance-criteria checklist.
**Owner:** [your name]
**Target agent:** Coding agent (Claude Code or equivalent)
**Last updated:** 2026-07-11

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

- [x] Full Camera Roll (tested with at least 5,000 mixed photo/video items, including several GB of 4K video) transfers successfully end-to-end via USB. — Validated 2026-07-10/11 against a real ~12,179-item / 141 GB library (well beyond the 5,000-item bar); 12,073 verified, 102 permanently failed on files the device itself refuses to open (`AfcFileNotFoundError`, likely iCloud-optimized originals not locally present — not a transfer bug). See `docs/DEVELOPMENT.md`.
- [x] Physically unplugging the cable mid-transfer and reconnecting within 5 minutes resumes without re-copying already-confirmed bytes. — Validated repeatedly during the same session, including real `[WinError 10053]`/`ConnectionResetError` disconnects; required several real bugs to be fixed first (see `docs/DEVELOPMENT.md`'s "Known gaps" for the full list — premature "Free Up Space" transition, stale-client resume race, per-file-vs-disconnect misclassification, a concurrency livelock).
- [x] Killing the app process mid-transfer and relaunching resumes correctly from persisted SQLite state. — Validated: the app was force-closed and relaunched multiple times mid-transfer, including once after several hours idle, and correctly resumed from the last persisted item each time.
- [x] No file is ever offered for deletion before its `verified` status is set. — Holds by construction (`app/services/verification.py`, `_run_transfer_then_verify`'s `OUTCOME_DRAINED` gating); not separately hardware-tested this round since the delete step itself wasn't exercised on the large-library run (see the next two items).
- [ ] Deleting a batch of verified files actually frees the reported space on the device (spot-checked against device storage settings). — Not yet exercised on the large-library validation run (transfer + verify only); was validated earlier against a smaller ~2,172-item device (see `docs/DEVELOPMENT.md`).
- [ ] Live Photo pairs are never split during delete. — Same status as above: logic exists (`app/services/delete_service.py`), not yet re-exercised on the large-library run.
- [x] Running the app a second time against the same (now partially-cleaned) library only transfers new items since the last run. — Validated: repeated `library.enumerate` calls across the multi-day session correctly grew the manifest as new photos were added (12,137 → 12,174 → 12,177 → 12,179) without re-transferring already-verified items, and `requeue_missing_local_files` correctly detected/re-queued items whose local copy went missing.
- [x] Works with zero manual driver installation steps on a clean Windows 11 machine that has never had iTunes installed. — The driver-missing detection and UI banner path is validated (`driver_missing`/`driver_available` events, see `docs/DEVELOPMENT.md`); the *silent, zero-click* installation ideal from this criterion's original wording isn't possible at all — Apple Mobile Device Support can't be redistributed by a third-party installer (§9 open question 2) — so this is met via the actionable-banner fallback, not silent bundling.

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
- **Exact + near-duplicate detection** — promoted to a detailed, ready-to-build design as of 2026-07-11: see §11 (**Library Cleanup Module**) for the full spec (scope, data model, engine, acceptance criteria). Prioritized first among this list per that design discussion.
- **Burst photo grouping** — detect burst sequences (consecutive timestamps, similar EXIF) and suggest keeping only the best N of a burst. Deferred until §11 ships — depends on near-duplicate detection already existing (§11.6).
- **Screenshot detection** — flag files matching iOS screenshot dimensions/metadata for an easy bulk-review/delete pass. Still unprioritized (§11.6).
- **Blurry/low-quality photo detection** — lightweight on-device (laptop-side, fully local, no cloud ML API) blur scoring to flag candidates for review. Deferred — heaviest computationally and most subjective to act on of this list (§11.6).
- **Large video flagging** — surface the largest video files first, since video is typically the dominant contributor to storage pressure.
- All smart-cleanup suggestions must be **review-and-confirm**, never auto-delete, consistent with the MVP's verify-before-delete philosophy — carried forward as a hard constraint into §11 (FR-L3).

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
3. ~~**AFC seek/resume support:**~~ **Resolved (2026-07-11):** `pymobiledevice3`'s `AfcService` has no `fseek` at all — confirmed by reading its source and by thousands of real `seek unsupported, restarting` events across a real ~141 GB transfer. The safer default was correct: resume at the file level (one sequential handle per file, matching the live cursor), restart at the byte level whenever a requested offset doesn't match — never attempt a true byte-level seek. See `docs/DEVELOPMENT.md`.
4. **Live Photo detection reliability:** confirm the most robust method for pairing the image and `.MOV` components (filename convention vs. metadata) across iOS versions before relying on it for the delete-safety guarantee in FR-8.
5. **Large library enumeration performance:** validate AFC directory listing performance against very large libraries (20,000+ items) and design pagination/incremental enumeration if a full upfront listing proves slow. Partial data point (2026-07-11): a full enumeration of a ~12,179-item library completed in under a minute; still not the 20,000+ target this question asks for, and re-enumeration (upsert-only against an already-populated manifest) was consistently faster than the first pass, so incremental-enumeration pressure may be lower than originally assumed — not yet enough evidence to close this question.
6. **Naming/trademark check:** before public launch, do a basic trademark and app-store-naming-guideline check on "E-iClean" (and the "i-" prefix generally) — this is a quick legal/branding sanity check, not a development task, but should not be skipped silently.

---

## 10. References / Libraries

- pymobiledevice3 — https://github.com/doronz88/pymobiledevice3
- libimobiledevice project (reference protocol documentation) — https://libimobiledevice.org/
- Tauri — https://tauri.app/
- Apple Mobile Device Support — distributed as part of iTunes / the "Apple Devices" app on Microsoft Store; confirm current standalone redistributable availability at implementation time (see Open Question 2).
- Pillow — https://python-pillow.org/ — image decode, needed for perceptual hashing (§11.4). Not yet a `backend/requirements.txt` dependency; add when §11 implementation starts.
- `imagehash` (or equivalent perceptual-hashing library built on Pillow) — https://github.com/JohannesBuchner/imagehash — candidate for the near-duplicate perceptual-hash implementation in §11.4. Confirm this specific library at implementation time rather than assuming it — an explicit "verify, don't assume" item per this doc's own convention (§9).

---

## 11. Library Cleanup Module (Post-MVP, Detailed Design)

**Status:** Implemented 2026-07-11, ahead of MVP acceptance criteria (§5.10) being
fully closed out — built at the user's explicit direction rather than following this
doc's own default sequencing (AGENTS.md §15 normally holds roadmap work until MVP is
done). Backend (scan engine, safe-delete service, IPC surface) and frontend (new
"Clean My Library" mode, review UI with a full-size comparison view) are both in
place and validated against a real ~1,264-file folder (27 duplicate groups correctly
found and reviewable). See `docs/DEVELOPMENT.md` for implementation notes and the bug
found via that live test (a silently-swallowed frontend error left the UI stuck on
"Scanning..." even though the backend had finished - fixed). Not yet validated: an
actual delete/move confirmed end-to-end by a user (compare-view UX was validated
live; the delete action itself has automated test coverage but not a live click-through
at time of writing).

### 11.0 Scope decisions (from the design discussion)

- **v1 covers exact + near-duplicate detection only.** Burst-sequence grouping and
  blur detection remain deferred (§11.6) until this ships and proves useful — burst
  grouping in particular depends on near-duplicate detection already existing.
- **Fully decoupled from iPhone/device connection.** Operates on any local folder(s)
  the user points it at, independent of the transfer flow (§5) entirely — not scoped
  to E-iClean's own transfer destinations only. No AFC, no `devices` table, no
  `device_udid` anywhere in this module's data model.
- **A separate top-level mode in the same app** ("Clean My Library" or similar name,
  TBD at implementation time), not a post-transfer step and not merged into the
  existing iPhone-connected screen flow (§5.5's state machine is untouched by this).
- **Review-and-confirm only**, consistent with §5.6's verify-before-delete philosophy
  (carried forward from §8.2's existing constraint) — suggestions surface information
  (duplicate groups, similarity/size/date) only. **Nothing is pre-selected**; the user
  makes every keep/delete decision explicitly, file by file. No "smart defaults" in v1.
- **Safe deletion by default (added 2026-07-11).** Confirmed deletions default to a
  *move*, not a delete: files are relocated to a sibling `<root>-delete` folder
  (§11.5) rather than removed outright, so the user can spot-check the result in
  Explorer before anything is actually gone. This is arguably more important here
  than in §5.6's iPhone-delete flow, since duplicate/blur detection is inherently
  probabilistic (perceptual similarity, not a byte-exact/checksum guarantee) in a way
  the transfer engine's verify step isn't — a staging folder is the safety net for
  that extra uncertainty. Permanent delete remains available as an explicit opt-out,
  not the default. Scoped entirely to this module — §5.6's iPhone delete flow (AFC
  `remove`) is unaffected and unrelated; there's no local "move" equivalent on the
  phone's filesystem, and its existing verify-before-delete guarantee already covers
  that case differently.

### 11.1 User Flow

1. From the app's home/nav, user switches to "Clean My Library" mode — independent of
   any iPhone connection state (can be used with no iPhone connected at all).
2. User picks one or more local folders to scan (native folder picker — the `dialog`
   plugin already used for the transfer destination picker, §5.7 point 3, applies
   here unchanged).
3. App walks the folder tree, hashing each image file (§11.4) with progress feedback;
   skips re-hashing files whose path + size + modified-time haven't changed since a
   prior scan of the same folder.
4. App groups files by exact match (identical content hash) and near-match
   (perceptual hash within a similarity threshold), and presents duplicate groups for
   review.
5. For each group, user sees thumbnails + metadata (file size, path, modified date)
   for every member and manually selects which to delete; nothing is pre-selected.
6. Before confirming, user sees a checkbox — **"Move to delete folder instead of
   permanently deleting"** — checked by default. User confirms the batch action; app
   either moves the selected files to `<root>-delete` (preserving their relative
   path, §11.5) or permanently deletes them from local disk, per the checkbox, and
   reports space freed either way.

### 11.2 Functional Requirements

| ID | Requirement |
|---|---|
| FR-L1 | App MUST scan a user-chosen local folder tree (not limited to E-iClean's own transfer destinations) for image files. |
| FR-L2 | App MUST detect exact duplicates via content hash (SHA-256) and near-duplicates via perceptual hash within a configurable similarity threshold. |
| FR-L3 | App MUST NOT pre-select any file for deletion — every deletion in this module requires explicit per-group, per-file user selection. |
| FR-L4 | App MUST persist scan state so a large scan (thousands of files) survives an app restart without re-hashing unchanged files. |
| FR-L5 | App MUST show enough metadata per duplicate-group member (thumbnail, size, path, modified date) for the user to make an informed keep/delete choice without leaving the app. |
| FR-L6 | Deleting a file in this module MUST be a plain local-disk operation, distinct from (and never touching) any iPhone/AFC state — this module has no device dependency at all. |
| FR-L7 | App MUST default confirmed deletions to a *move* into a sibling `<scanned-root>-delete` folder (preserving the file's path relative to the scan root), not a permanent delete, unless the user explicitly opts into permanent delete via the confirmation checkbox. |
| FR-L8 | App MUST exclude any folder matching the `<root>-delete` naming convention from being scanned (as a scan root itself, or discovered as a subfolder) — otherwise a rescan would recurse into the module's own staging area and re-flag already-moved files. |
| FR-L9 | App MUST NOT automatically empty or permanently purge a `<root>-delete` folder — the user reviews and clears it manually (e.g. via Explorer) once satisfied. (Open item, §11.6: an in-app "empty this delete folder" action is a natural fast-follow, not required for v1.) |

### 11.3 Data Model (SQLite — new tables, independent of `devices`/`transfer_items`)

```sql
CREATE TABLE library_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    modified_at TIMESTAMP,
    content_hash TEXT,          -- sha256, populated after hashing
    perceptual_hash TEXT,       -- e.g. 64-bit dHash, hex-encoded
    last_scanned_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'active',
        -- active | moved_to_delete_folder | deleted
        -- moved_to_delete_folder: local_path has been updated to its new location
        -- under <root>-delete (FR-L7); the row is kept, not removed, so the app can
        -- later show/manage delete-folder contents (e.g. total size pending review).
    scan_root TEXT NOT NULL     -- the root folder this file was discovered under -
                                 -- lets FR-L8's exclusion rule and FR-L7's relative-
                                 -- path move both be computed without re-deriving it.
);

CREATE TABLE duplicate_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_type TEXT NOT NULL,   -- exact | near
    similarity_score REAL,      -- NULL for exact groups
    created_at TIMESTAMP
);

CREATE TABLE duplicate_group_members (
    group_id INTEGER NOT NULL REFERENCES duplicate_groups(id),
    library_file_id INTEGER NOT NULL REFERENCES library_files(id),
    PRIMARY KEY (group_id, library_file_id)
);
```

### 11.4 Scan Engine Design

- **Hashing is CPU-bound and must run off the UI thread**, parallelized across a
  worker pool. Unlike the transfer engine's single-AFC-channel constraint (§5.4 —
  sequential by necessity), local disk reads have no such limitation; concurrency
  here should scale with available cores.
- Compute both a content hash (SHA-256, cheap) and a perceptual hash (requires image
  decode — needs an image library dependency not currently in
  `backend/requirements.txt`, see §10) per file.
- Skip files already hashed in a prior scan whose `size_bytes`/`modified_at` haven't
  changed — mirrors `requeue_missing_local_files`'s self-healing philosophy from the
  transfer engine (`backend/app/services/enumeration.py`), but in reverse
  (skip-if-unchanged rather than re-check-if-claimed-done).
- Group formation: exact groups from identical `content_hash`; near groups from
  `perceptual_hash` Hamming distance under a threshold, excluding files already in an
  exact group.
- Progress reported via the same event-emission pattern as transfer
  (`transfer_progress`'s role, see `docs/ipc_protocol.md`) — a scan of thousands of
  files must not block or freeze the UI. Exact event name/shape TBD at implementation
  time (this doc doesn't commit to wire format the way §5's MVP does yet — see
  `docs/ipc_protocol.md`'s own instruction to update it in the same commit as the
  code change, once this module exists).

### 11.5 Deletion (default: safe move to a `-delete` folder)

- Local disk operation only — no AFC, no device. Runs in confirmed batches (mirroring
  §5.6 point 5's transfer-delete batching), with per-file failure reporting rather
  than one blocking call.
- **Default mode (checkbox checked, FR-L7): move, not delete.** For a scan root
  `D:\Photos\MyPictures`, the staging folder is the sibling
  `D:\Photos\MyPictures-delete` — suffix applied to the *scanned root* specifically,
  never to individual subfolders. A file at `MyPictures\2020\Summer\IMG_001.jpg`
  moves to `MyPictures-delete\2020\Summer\IMG_001.jpg`, i.e. its path relative to
  `scan_root` is preserved intact under the staging folder. Directories are created
  as needed (`mkdir -p` semantics) — no special handling beyond that.
- **Collision handling on move:** if the target path under `<root>-delete` already
  exists (e.g. a prior cleanup pass left a same-named file there, or the user
  restored one manually and a later pass re-flags a different file with the same
  relative path), reuse the transfer engine's existing disambiguation convention —
  fold a distinguishing parent-folder-name suffix into the filename
  (`TransferEngine._local_relative_path`, `backend/app/services/transfer_engine.py`)
  — rather than inventing a second scheme for the same class of problem.
- **Permanent mode (checkbox unchecked):** files are deleted outright, as originally
  scoped (no staging folder involved).
- Either way, update `library_files.status` (`moved_to_delete_folder` or `deleted`,
  with `local_path` updated to the new location for the move case) and any
  `duplicate_group_members` reference, updating the group accordingly — a group with
  one remaining active member is no longer a duplicate group.
- **Scan exclusion (FR-L8):** the scan walk (§11.4) must skip any folder matching the
  `<root>-delete` naming convention, both as a would-be scan root and as a discovered
  subfolder during a walk — otherwise a later rescan of `MyPictures` would descend
  into `MyPictures-delete` (if nested rather than sibling — reinforcing why it must be
  a *sibling*, not a subfolder, of the scanned root) or a separate top-level scan of
  the parent folder would treat already-moved files as live library content again.
- **Emptying the staging folder is out of scope for v1** (FR-L9) — the user manages
  it manually via Explorer once satisfied. An in-app "empty this delete folder /
  permanently delete now" action is a reasonable fast-follow (§11.6) once the basic
  move-based flow is validated with real use.

### 11.6 Explicitly Deferred Within This Module

- **Burst-sequence grouping** (timestamp + similarity clustering) — depends on
  near-duplicate detection existing first.
- **Blur/quality scoring** (e.g. Laplacian variance) — heaviest computationally of
  the original Smart Cleanup ideas (§8.2), and the most subjective to act on.
- **Screenshot detection** — unprioritized.
- **Cross-referencing against already-transferred iPhone photos** (`transfer_items`)
  to detect "this is already backed up elsewhere" — not decided either way yet, worth
  a follow-up brainstorm once v1 ships. Would be the first point of contact between
  this module and the transfer engine's data model if ever built.
- **In-app "empty the delete folder" / permanently purge action** (FR-L9) — v1 relies
  on the user clearing `<root>-delete` manually via Explorer. A natural fast-follow
  once the move-based safe-delete flow (§11.5) is validated with real use, not
  required to ship the feature initially.

---

*End of specification. This document is intended as the working brief for an autonomous coding agent. The agent should treat §5 (MVP) as the immediate, fully-scoped implementation target, §11 (Library Cleanup Module) as the next fully-scoped target once §5's acceptance criteria are closed out, and the rest of §8 (Roadmap) as context only — not to be built until then.*
