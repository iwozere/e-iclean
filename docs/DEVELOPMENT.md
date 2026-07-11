# Developing E-iClean

This is the contributor/developer guide. If you're looking for what E-iClean does
and how to install it, see the top-level [README.md](../README.md). See
`project_specification.md` for the full product spec and `ipc_protocol.md` for the
Rust&harr;Python contract. Contributor conventions live in `../AGENTS.md` — read it
before making changes.

**Status:** MVP core loop validated end-to-end against real hardware (v0.1.5). A real
~12,179-item / 141 GB iPhone library has been fully transferred, verified (12,073
verified, 102 permanently failed on files the device itself can't open, 4 recovered
from an orphaned `in_progress` state), and survived multiple real disconnects, several
full app restarts mid-transfer, and an overnight idle period, all resuming correctly.
Remaining before MVP acceptance criteria (spec §5.10) are fully closed: an end-to-end
**delete** ("Free Up Space") pass on this same large library hasn't been exercised yet
(transfer + verify only, this round), and today's fixes haven't been re-validated
against the **frozen** PyInstaller build specifically (dev mode only so far — see
"Freezing the backend" below). See "Known gaps" below for the full list, including
several real bugs found and fixed via this real-device session.

**Also implemented (v0.1.8, 2026-07-11):** the Library Cleanup module (spec §11) - a
second, independent "Clean My Library" mode for finding exact/near-duplicate photos in
any local folder, with a safe move-to-`<root>-delete` default instead of permanent
delete. Built ahead of MVP being fully closed out, at the user's explicit request (see
spec §11's status note). Validated against a real ~1,264-file folder.

## Architecture

```
src/          vanilla JS/HTML/CSS web UI (no build step)
src-tauri/    Rust shell: window, spawns/owns the Python sidecar, IPC bridge
backend/      Python sidecar: pymobiledevice3, transfer engine, SQLite state
docs/         product spec + IPC protocol contract
```

The Rust shell and Python backend talk over newline-delimited JSON-RPC on stdio — no
HTTP, no network listener. See `ipc_protocol.md`.

## Prerequisites

- **Rust** (stable, MSVC toolchain) + Tauri CLI (`cargo install tauri-cli --locked`).
  On this machine, Rust lives at `D:\tools\cargo` / `D:\tools\rustup`
  (`CARGO_HOME` / `RUSTUP_HOME`), already on `PATH`.
- **Python 3.13**, with a project-local venv at `backend/.venv`.
- **Visual Studio Build Tools** (C++ workload) for the MSVC linker — already present
  on this machine via the existing VS2022 Community install.
- **WebView2 Runtime** — present on Windows 10 21H2+/11 by default.

## Setup

```powershell
cd backend
py -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
cd ..
```

## Running (dev)

```powershell
cargo tauri dev
```

This launches the Rust shell, which spawns `backend/.venv/Scripts/python.exe run.py`
as the sidecar and loads `src/index.html` directly (no dev server — `frontendDist`
points straight at `src/`, matching the no-build-step frontend).

Verified with a real smoke test on this machine: the shell builds, spawns the sidecar,
disables USB selective suspend, and the device watcher correctly reports (and logs,
without crashing) that no iPhone/Apple Mobile Device Support is present — this dev
machine has neither. Full device pairing/transfer flows still need real hardware.

To iterate on the backend alone (fast feedback without the Rust shell), run the test
suite rather than the sidecar directly — the sidecar just blocks reading stdio:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
```

## Building

```powershell
cargo tauri build
```

Production builds need the Python backend frozen into a standalone executable
(PyInstaller), referenced from `src-tauri/src/sidecar.rs` instead of the dev-mode venv
path. See "Freezing the backend" below for current status.

## Freezing the backend (PyInstaller)

See `backend/BUILD.md` for the frozen-executable build process, current status, and
known issues with bundling `pymobiledevice3`'s native dependencies.

## Known gaps (tracked, not silently skipped)

These are explicitly deferred, either because they need hardware/licensing decisions
this environment can't make, or because they're follow-up work beyond the initial
scaffold:

- **Fixed: filename collisions across DCIM folders silently clobbered files.**
  Surfaced by a real ~12k-item transfer: 1094 items failed verification with "local
  file size doesn't match," and the local file's actual content belonged to a
  *different* item entirely. Root cause: `transfer_engine.py` built the local path
  from the bare filename only (`destination / file_name`), but iPhone libraries
  spread files across many `DCIM/NNNAPPLE` folders whose NNN and IMG_/MOV_ counters
  both wrap over a large enough library — two different remote files can share one
  bare filename, and the second one to finish writing silently overwrote the first.
  `transfer.start` also now resets any `failed` items back to `pending`
  (`handlers.py::_retry_failed_items`) — previously a failed item was stuck forever
  since only `pending`/`partial` items are ever revisited, so the ~1000 items
  corrupted by this bug had no way to recover short of re-enumerating the whole
  device from scratch. **Caveat**: files that already collided under the old code
  have a stray leftover at the old path (whichever item's content "won" the race)
  even after retrying with the fix below — that file isn't automatically cleaned up,
  since guessing which of two plausible files to delete felt riskier than leaving it
  for manual review.
- **Destination layout: date-based nesting (`YYYY-MM/file_name`), not flat.**
  Chosen over both a flat destination (the collision-prone original) and mirroring
  Apple's raw `DCIM/NNNAPPLE` numbering (collision-proof, but `104APPLE` means
  nothing to a user browsing their backup later) — see `TransferEngine.
  _local_relative_path` for the full reasoning. Bucketed by `remote_modified_at`
  (`"unknown-date"` if AFC didn't report one). Date nesting mostly avoids collisions
  by construction but doesn't guarantee it — two items can still share both a
  filename and a capture month — so the DCIM-parent-folder-name suffix fallback from
  the previous fix still applies, now scoped to "same month" instead of "anywhere in
  the device," e.g. `2026-07/IMG_0005 (100APPLE).HEIC`. Fixing this also surfaced a
  second, independent bug: `PymobiledeviceAfcClient.list_directory`/`file_info`
  (`backend/app/device/afc_client.py`) were never populating `AfcFileInfo.
  modified_at` from AFC's `stat()` at all — every real-device file would have landed
  in the `"unknown-date"` bucket regardless of its real date, silently defeating this
  feature for anything except `MockAfcClient`-based tests. Fixed alongside the
  nesting change itself.
  `TransferItem.remote_path` (the full AFC source path) and `local_path` (the full
  destination path) are both already persisted per-item regardless of this nesting
  scheme — the exact source path was always reconstructable from SQLite, this change
  only affects where `local_path` itself points.
- **Fixed: SQLite verification state was trusted forever, never re-checked against
  disk.** Real user report: they deleted the whole destination folder and
  reconnected the same, already-fully-verified device — it jumped straight to Free
  Up Space instead of re-downloading anything, since every item still said
  `verified` in the DB and nothing had ever asked whether the file it claims to have
  written was still actually there. Fixed by `app/services/enumeration.
  py::requeue_missing_local_files`: every `library.enumerate` (device connect, and
  the new "Re-check Library" button) now stats each `copied`/`verified` item's
  `local_path` and resets it to `pending` if the file is missing or its size no
  longer matches, so the next `transfer.start` naturally re-copies it. Symmetric
  with the existing `_retry_failed_items` (`handlers.py`, resets `failed` ->
  `pending` on `transfer.start`) - together these mean SQLite state is now
  self-healing against the real filesystem instead of assumed correct forever.
- **Added: "Re-check Library" button** (Free Up Space and Done screens) — until now
  there was no way back from those screens short of physically disconnecting and
  reconnecting the device, which a user flagged directly. It re-runs
  `library.enumerate` (triggering the self-healing check above) and returns to the
  Ready-to-transfer screen. Deliberately scoped to this one action rather than a
  generic multi-screen Back button/history stack — most other screen transitions
  don't have an obviously "correct" back-target (e.g. mid-transfer), so a targeted
  fix for the reported problem seemed better than open-ended navigation surgery.
- **Fixed: the frozen backend's console window was visible alongside the app
  window.** `--console` is required for stdio IPC (backend/BUILD.md), but as a real
  child process (not just background stdio plumbing) that also means a visible
  console window in release builds — a user noticed two windows on launch.
  `src-tauri/src/sidecar.rs` now passes `CREATE_NO_WINDOW` when spawning the
  release-mode sidecar (Windows-only, gated behind `cfg!(debug_assertions)` being
  false) — stderr is already captured and forwarded into the Rust log regardless, so
  the window itself had no debugging value. Left visible in dev mode intentionally,
  in case seeing the raw Python console directly is useful while iterating.

- **Real-device testing: connect/enumerate/transfer/verify are now extensively
  exercised; delete is not yet.** Earlier attempts hit an immediate `library.enumerate`
  crash (see the next bullet) that blocked everything past pairing. That's long since
  fixed - a real ~12,179-item / 141 GB library has since been fully enumerated,
  transferred, and verified end-to-end (12,073 verified, see the bug list above for
  everything that surfaced and got fixed along the way), including real cable/USB
  disconnects, several full app restarts mid-transfer, and an overnight idle gap, all
  resuming correctly. Spec §5.10's "5,000+ items" criterion is comfortably exceeded.
  Still open: an end-to-end **delete** ("Free Up Space") pass on this same library
  (transfer + verify only, so far), and re-validating against the **frozen**
  PyInstaller build specifically (all of the above was dev mode). All backend logic
  remains additionally tested against a mocked AFC client
  (`app.device.afc_client.MockAfcClient`) for fast, hardware-free regression coverage.
- **`AfcService.listdir`/`stat`/`fopen`/`rm` are coroutines too — the earlier "not
  async" claim was a false negative from a flawed check, now fixed.** The first real
  device connection surfaced `TypeError: 'coroutine' object is not iterable` from
  `list_directory`. Root cause: these four methods are decorated with
  `@path_to_str()`, which wraps the real `async def` in a plain `def` — so
  `inspect.iscoroutinefunction()` (what the earlier investigation relied on, see
  git history) reports `False` even though calling it still returns a coroutine that
  must be awaited. In fact **every** `AfcService` method used here
  (`connect/fread/fwrite/fclose/close` *and* `listdir/stat/fopen/rm`) is a coroutine
  function; there was no non-async subset. All call sites in
  `backend/app/device/afc_client.py` now wrap every `self._afc.*` call in
  `asyncio.run(...)`. Lesson: don't trust `inspect.iscoroutinefunction` through an
  unfamiliar decorator — a live smoke test is what actually caught this, twice.
- **AFC seek/resume (spec §9 open question 3) — resolved, confirmed against real
  hardware.** Confirmed by reading pymobiledevice3 9.32.0's source directly:
  `AfcService` has no `fseek` at all — `fopen`/`fread`/`fclose` only support one
  sequential cursor per handle. `PymobiledeviceAfcClient` (see
  `backend/app/device/afc_client.py`) is written around that constraint: it keeps one
  handle open across sequential chunk reads within a file, and raises
  `SeekNotSupportedError` (triggering the documented single-file-restart fallback in
  `transfer_engine.py`) whenever a requested offset doesn't match the live sequential
  position — e.g. resuming after an app restart. Now confirmed live against a real
  device across many thousands of `seek unsupported, restarting item_id=...` events:
  the single-file restart-from-zero fallback works as designed. The default answer
  from spec §9.3 stands - "resume at the file level, restart at the byte level" - not
  the alternative (byte-level seek), which this AFC surface doesn't support at all.
- **pymobiledevice3 API is more async than the spec assumed, now confirmed and
  fixed**: `usbmux.list_devices`, `lockdown.create_using_usbmux`, and every used
  `AfcService` method (`connect/fread/fwrite/fclose/close/listdir/stat/fopen/rm`) are
  coroutine functions in 9.32.0 — see the `listdir`/`stat`/`fopen`/`rm` bullet above
  for the decorator gotcha that hid this. A real `cargo tauri dev` smoke test on this
  machine caught the mismatch (an unawaited-coroutine warning and a dead device
  watcher loop) before this was fixed — see `backend/app/device/discovery.py`,
  `pairing.py`, and `afc_client.py`. Re-verify if `pymobiledevice3` is upgraded.
- **Live Photo pairing uses a filename-stem heuristic**, not Apple's actual asset
  metadata (spec §9, open question 4) — see `backend/app/services/live_photo.py`.
- **Installer / driver bundling: sidecar wiring done; bundling ruled out, not just
  deferred** (spec §5.8, §9 open question 2): PyInstaller freezing, `externalBin`
  wiring, and a full `cargo tauri build` producing working MSI/NSIS installers are all
  validated end-to-end (see `backend/BUILD.md`). Apple Mobile Device Support cannot be
  bundled into the installer at all — it's proprietary Apple software distributed only
  through Apple's/Microsoft's own channels, not something a third-party installer may
  redistribute. Instead, the backend now detects the missing-driver condition
  (`ConnectionFailedToUsbmuxdError`, see `backend/app/device/discovery.py`) and the UI
  shows an actionable banner with download links (`driver_missing`/`driver_available`
  events, `docs/ipc_protocol.md`). After installing the driver via that banner, a real
  device connected, transferred, and verified a full library end-to-end in dev mode
  (2172 items, ~6.3 GB) — see the `PymobiledeviceAfcClient` persistent-event-loop
  bullet below for the bugs that surfaced along the way. Still open: the same
  validation against the **frozen** PyInstaller exe specifically, not just dev mode
  (see `backend/BUILD.md`'s "Known issues").
- **`PymobiledeviceAfcClient` needs one persistent event loop for its whole session,
  not `asyncio.run()` per call** — found via the first real end-to-end transfer, which
  died with `ConnectionResetError: Connection lost` a few calls into enumeration.
  `asyncio.run()` tears its event loop down when the coroutine returns, but the AFC
  connection's stream reader/writer are bound to whichever loop created them in
  `__init__` - every subsequent call from a different, short-lived loop touches a
  transport whose owning loop no longer exists. Fixed by running a single background
  thread with one persistent loop for the client's lifetime
  (`backend/app/device/afc_client.py`, `asyncio.run_coroutine_threadsafe`). Related:
  nothing was closing a device's `PymobiledeviceAfcClient` on disconnect, leaking that
  thread/connection forever and potentially interfering with the next reconnect -
  `backend/app/ipc/handlers.py::handle_device_connect` now closes any stale client for
  the same udid before creating a fresh one.
- **Native folder picker is wired in; "open in Explorer" is still stubbed.** The
  `dialog` plugin (`src-tauri/src/lib.rs`, `dialog:default` capability) now backs
  `src/main.js`'s `onChooseDestination` with a real native folder picker instead of a
  text prompt. The `opener` plugin (`opener:default`, `withGlobalTauri: true`) is
  wired for the driver-missing banner's download links (`openUrl`), but its
  `revealItemInDir` isn't used yet for "Open in Explorer" on the done screen — that's
  still a `window.alert` stub.
- **Settings persistence is implemented**: a minimal `settings` key/value table
  (`app.models.Setting`, `backend/app/services/settings_service.py`) backs
  `settings.get`/`settings.set`, so `destination_default` (spec §5.7 points 3, 8) now
  survives an app restart instead of resetting to frontend-only in-memory state every
  time. Not in the spec's §5.3 schema listing (written before this was wired up) -
  see the model's docstring for why a generic key/value table rather than a dedicated
  column per setting.
- **Destination free-space warning** (spec §5.7 point 3) is not yet implemented.
- **Fixed: a mid-transfer disconnect jumped straight to "Free Up Space" and reported
  thousands of never-attempted items as unrecoverable failures.** Root cause:
  `TransferEngine.run()` (`backend/app/services/transfer_engine.py`) returned `None`
  on every exit path — drained, paused, cancelled, *or* disconnected were all
  indistinguishable to the caller. `handlers.py::_run_transfer_then_verify` ran
  verification and emitted `verification_complete` unconditionally after every
  `run()` call, so a disconnect after copying 1 of 12,174 items looked identical to
  "the queue actually finished," and the UI reported the other 12,173 untouched items
  as "could not be copied or verified." Fixed by giving `run()` a real return value
  (`OUTCOME_DRAINED`/`PAUSED`/`CANCELLED`/`DISCONNECTED`) and only running
  verification on `OUTCOME_DRAINED` — see `docs/ipc_protocol.md`'s
  `verification_complete` entry.
- **Fixed: the reconnect-triggered auto-resume raced its own client teardown,
  compounding the bug above.** On reconnect, the backend used to auto-resume via the
  raw usbmux `device_connected` event (`app/main.py`) — which fires *before* the
  trust-wait/AFC-handshake that `handle_device_connect` performs. That handshake
  closes the previous `AfcClient` and installs a fresh one; the auto-resume, racing
  ahead of it, kept reading through the client that was mid-teardown, immediately
  re-disconnected, and (via the bug above) fired another spurious
  `verification_complete` — which is what made "Start Transfer" look like a no-op
  right after a user clicked "Re-check Library": the screen was being silently
  snapped back to "Free Up Space" by a stale background task the user never
  triggered. Fixed by moving the resume trigger into `handle_device_connect` itself,
  after its own fresh `AfcClient` is confirmed in place
  (`TransferEngine.set_afc`), and removing the racy call from `app/main.py`.
- **Fixed: a single missing/inaccessible file on the device was misclassified as a
  full device disconnect, pausing the entire queue.** The biggest real-world impact
  bug found this session. `_read_chunk_safe` used to wrap *any* exception from
  `AfcClient.read_chunk` as `DEVICE_DISCONNECTED`. Against the real ~12k-item
  library, a meaningful fraction of files (~13-20% in some ranges) raise
  `pymobiledevice3.exceptions.AfcFileNotFoundError` ("[Errno 8] ... status: 8") when
  opened - the device answering normally, just with an error status, most likely
  files whose full-resolution originals aren't actually downloaded to the device
  (iCloud "Optimize Storage"). Every one of these was pausing the *entire* transfer
  and showing a misleading "Connection lost" banner for something that was never a
  connection problem. Fixed with a device-layer distinction
  (`backend/app/device/afc_client.py`): `AfcException` (and subclasses) means "the
  device answered, just with an error for this file" and is left to propagate as a
  normal per-item failure; only a new `AfcConnectionLostError` - raised after a
  bounded retry-with-backoff (`AFC_READ_RETRY_ATTEMPTS`/`AFC_READ_RETRY_DELAY_SECONDS`
  in `app/config.py`) on genuine transport-level failures - maps to
  `DEVICE_DISCONNECTED`. The retry loop also absorbs brief real connection blips
  (confirmed live: `OSError [WinError 10053]`/`ConnectionResetError`) without ever
  surfacing "Connection lost" to the user at all.
- **Fixed: a reconnect racing a still-in-flight `run()` could spawn a second,
  fully-concurrent transfer loop on the same engine.** `TRANSFER_CONCURRENCY` is
  explicitly 1 (see the module docstring in `transfer_engine.py`), but nothing
  actually enforced that across a reconnect: `resume_session_if_paused` fired a new
  `run()` via `asyncio.create_task` without checking whether the previous `run()` (on
  the engine's dying, pre-reconnect client) had actually returned yet. Confirmed live
  against a real device: a brief real disconnect produced two `run()` loops racing
  the same item queue for a sub-second window; because both loops share one
  `PymobiledeviceAfcClient` instance (which has a single mutable open-file-handle
  state, not reentrant for concurrent *different* files), a second, longer-lived
  occurrence of this race caused a genuine livelock - two loops perpetually clobbering
  each other's open-file state, throwing `SeekNotSupportedError`, restarting from
  byte 0, and immediately being clobbered again, forever, with zero forward progress
  until the app was restarted. Fixed with a per-engine `asyncio.Lock` around the body
  of `run()` (`TransferEngine._run_lock`) - a second call now queues up and starts
  only once the first has fully exited, preserving the auto-continuation behavior
  (the resumed run picks up where the dying one left off) without ever running
  alongside it.
- **Fixed: the on-screen transfer progress (%, GB) was a purely in-memory,
  session-local counter that reset to 0 on every app restart**, even though the
  destination folder (and the DB) still remembered everything genuinely copied in
  earlier sessions. Confirmed live: after a restart mid-transfer, the UI showed "0% -
  650MB" while the destination folder already had 52 GB on disk from earlier
  sessions. Root cause: `src/main.js`'s `handleTransferProgress` summed
  `bytes_transferred` only from `transfer_progress` events received since the current
  page load (`state.transferredByItem`), with no seeding from persisted state. Fixed
  by having the backend compute and include an authoritative, DB-sourced
  `device_bytes_transferred` (sum across every item for the device) on every
  `transfer_progress` event (`TransferEngine._device_bytes_transferred`,
  `docs/ipc_protocol.md`) - the frontend now just displays that directly instead of
  reconstructing a running total itself.
- **Fixed: an item left `in_progress` when the app closes/crashes mid-copy was
  orphaned forever.** `_next_item_id` only ever selects `pending`/`partial`; nothing
  reset `in_progress` back to `pending` on the next `transfer.start`, unlike `failed`
  items (`handlers.py::_retry_failed_items`, existing behavior). Confirmed live: 4
  items were still stuck `in_progress` hours after the interrupted session that
  produced them. Nothing "in progress" can survive across a fresh `TransferEngine`
  anyway (no engine object persists the closure), so it's always safe to requeue -
  `_retry_failed_items` now resets both `failed` and `in_progress` items back to
  `pending`.
- **Fixed: a permanently-failed item left a stale, empty `.partial` file behind
  forever.** `_mark_failed` never cleaned up the `.partial` file its own attempt had
  created. Confirmed live: 102 zero-byte `.partial` files accumulated in
  `unknown-date` over one long session, one per permanently-failed item (see the
  `AfcFileNotFoundError` bug above - these files were never actually downloaded, so
  the `.partial` was 0 bytes, not real partial data). `_mark_failed` now deletes the
  item's `.partial` file (if any) after marking it failed.
- **Diagnostics gap, fixed in passing:** `TransferEngine.run()`'s `DEVICE_DISCONNECTED`
  branch used to log nothing at all, unlike the adjacent per-item-failure branch -
  found the hard way, debugging a live disconnect with an empty log for the exact
  window that mattered. Now logs item id, udid, and the underlying error detail.
- **Added: Library Cleanup module (spec §11) - "Clean My Library" mode, fully
  independent of the iPhone-transfer flow.** New backend services
  (`app/services/library_scan.py`, `library_delete.py`), new SQLite tables
  (`library_files`, `duplicate_groups`, `duplicate_group_members`), new IPC methods
  (`library_scan.start`/`.groups`, `library_delete.batch`, see `ipc_protocol.md`), and
  a new frontend mode (`src/index.html`'s `#mode-library`, wired in `main.js`/`ui.js`).
  Perceptual-hash near-duplicate detection uses `imagehash`/Pillow (new
  `backend/requirements.txt` deps). The filename-collision disambiguation convention
  from the transfer engine was extracted into a shared `app/utils/naming.py` rather
  than reimplemented, since `library_delete.py` needs the identical logic for its own
  collision case (spec §11.5).
  - **Fixed, found via a real ~1,264-file scan:** the scan itself (hashing, grouping -
    27 groups correctly found) completed successfully in the backend, but the UI
    stayed stuck on "Scanning..." forever. Root cause: `handleLibraryScanComplete`
    (`src/main.js`) had no try/catch around fetching and rendering the results: a
    failure there became an unhandled promise rejection (Tauri's event listener
    doesn't await the callback), invisible to the user, that also prevented the
    `setScreen("library-review")` call right after it from ever running. Now wrapped,
    so any future failure here shows a real error and returns to the idle screen
    instead of hanging silently.
  - **UX iteration from live user feedback:** the initial review grid's 140px
    thumbnails were too small to actually judge near-duplicates against each other
    (blur, framing, exposure). Fixed by making thumbnails somewhat bigger (170px) and,
    more importantly, adding a click-to-compare view: clicking a thumbnail relocates
    that group's real DOM nodes (not copies) into a large-size (380px) modal, so
    checkbox selection state can never drift out of sync between the grid and the
    comparison view - see `ui.js`'s `openCompareModal`/`closeCompareModal`.
  - **Enabling Tauri's asset protocol** (`assetProtocol.enable` + broad `scope: ["**"]`
    in `tauri.conf.json`) was required for thumbnails to load at all via
    `convertFileSrc` - scan roots are arbitrary user-picked folders, not a fixed known
    location, so the scope can't be narrowed the way a typical Tauri app might.
  - Not yet exercised live: an actual delete/move confirmed end-to-end via real
    clicks (automated test coverage exists for `library_delete.py`; the UI flow for
    it specifically hasn't had a live click-through at time of writing, unlike the
    scan+review flow which has).

## Toolchain notes

- Rust/Cargo were installed to `D:\tools` (not the default `%USERPROFILE%\.cargo`)
  per the project owner's preference — see AGENTS.md §13.
- MSVC Build Tools and WebView2 were already present on this machine; no additional
  install was needed for those.
