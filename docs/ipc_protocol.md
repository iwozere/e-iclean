# IPC Protocol — Rust shell <-> Python sidecar

Binding contract between `src-tauri/` and `backend/`. Both sides implement this exactly;
if you need to change it, update this doc in the same commit as the code change.

## Transport

- The Rust core spawns the Python sidecar as a child process (`backend/run.py` in dev,
  the frozen PyInstaller executable in production).
- Communication is **newline-delimited JSON** over the child's `stdin`/`stdout`. Each
  line is exactly one JSON object. `stderr` is reserved for the sidecar's own log
  output (human-readable, not protocol traffic) and is captured by the Rust side and
  appended to the combined app log.
- No HTTP, no sockets, no network listener of any kind.

## Message shapes

Three message kinds, distinguished by which fields are present:

### Request (Rust -> Python)

```json
{"id": "1", "method": "transfer.start", "params": {"device_udid": "...", "destination": "C:\\Photos"}}
```

- `id` is a string, unique per in-flight request, chosen by the Rust side.
- `method` is `"<noun>.<verb>"`, matching a handler in `backend/app/ipc/dispatcher.py`.
- `params` is an object (possibly empty `{}`), validated against a Pydantic schema in
  `backend/app/schemas.py` named `<Method>Params`.

### Response (Python -> Rust)

```json
{"id": "1", "result": {"session_id": 42}}
```

or, on failure:

```json
{"id": "1", "error": {"code": "device_disconnected", "message": "iPhone disconnected mid-transfer.", "detail": "..."}}
```

- Exactly one response per request, same `id`.
- `error.code` is one of the mapped error codes from `backend/app/utils/errors.py`
  (never a raw exception class name or protocol string — see AGENTS.md §7). `detail` is
  optional, technical, and only surfaced behind the "Copy diagnostic info" action.

### Notification (Python -> Rust, unsolicited)

```json
{"event": "transfer_progress", "data": {"item_id": 17, "bytes_transferred": 4194304, "remote_size_bytes": 8388608, "device_bytes_transferred": 55834574848}}
```

- No `id`. Not a reply to anything. Rust forwards each as a typed Tauri event
  (`events.rs`) to the webview, named identically to `event`.

## Methods (initial set — extend as MVP work proceeds, keep this list current)

| Method | Params | Result | Notes |
|---|---|---|---|
| `device.list` | `{}` | `{devices: [...]}` | Current usbmux-visible devices. |
| `device.connect` | `{udid}` | `{status, device}` | Begins pairing/trust flow if needed. |
| `library.enumerate` | `{udid}` | `{total_items, total_bytes}` | Builds/refreshes the transfer manifest in SQLite, and re-validates existing `copied`/`verified` items against disk, requeueing any as `pending` whose local file is missing or changed size (`app/services/enumeration.py::requeue_missing_local_files`) — called both on device connect and by the frontend's "Re-check Library" action. |
| `transfer.start` | `{udid, destination}` | `{session_id}` | Starts/resumes the queue. |
| `transfer.pause` | `{session_id}` | `{}` | Pauses without losing state. |
| `transfer.cancel` | `{session_id}` | `{}` | Cancels; partial files remain resumable later. |
| `verify.status` | `{session_id}` | `{verified_count, pending_count}` | Poll-or-event hybrid; events preferred. |
| `delete.batch` | `{item_ids: [...]}` | `{deleted_count, failures: [...]}` | Only ever called from an explicit user confirmation in the UI. |
| `settings.get` / `settings.set` | varies | varies | Destination folder default, concurrency, log location. |
| `library_scan.start` | `{root}` | `{}` | **Library Cleanup module (spec §11), fully independent of the methods above — no `udid` anywhere.** Fire-and-forget like `transfer.start`: scans `root` in the background (`app/services/library_scan.py`), reporting progress/completion via events, not the response. Refuses (raises) if `root` is itself a `*-delete` staging folder (FR-L8). |
| `library_scan.groups` | `{}` | `{groups: [{id, group_type, similarity_score, members: [{id, local_path, size_bytes, modified_at}]}]}` | Current duplicate groups from the most recent scan(s) — global across every folder ever scanned, not scoped to one root (see `library_scan._form_duplicate_groups`'s docstring for why). Thumbnails are not sent over IPC; the frontend renders them directly from `local_path` via Tauri's asset/`convertFileSrc` mechanism. |
| `library_delete.batch` | `{library_file_ids: [...], permanent: false}` | `{deleted_count, failures: [...]}` | Only ever called from an explicit user confirmation, same as `delete.batch` — but this is a **local-disk file move/delete**, not an AFC call, and never touches `transfer_items`/`devices` state (FR-L6). `permanent: false` (the default, and the UI's checked-by-default checkbox state) moves files to a sibling `<root>-delete` folder instead of deleting them (FR-L7); `permanent: true` is the explicit opt-out. |

## Events (initial set)

`device_connected`, `device_disconnected`, `awaiting_trust`, `enumeration_progress`,
`transfer_progress`, `connection_lost`, `connection_resumed`, `verification_progress`,
`delete_progress`, `backend_crashed` (Rust-originated, sidecar process died), `error`
(out-of-band error not tied to a specific request, e.g. a background watcher failure).

**Known gap:** `connection_resumed` is declared here and the frontend listens for it
(`src/main.js`), but nothing in the backend actually emits it — the reconnect banner is
currently cleared by a side effect of `refreshLibrary` succeeding (a successful
`library.enumerate` call is itself proof the device is reachable again), not by this
event. Fine functionally today, but the event itself is dead code; either wire it up
from `handle_device_connect` (`backend/app/ipc/handlers.py`) once a resumed transfer is
confirmed healthy, or remove it from both sides to stop implying a signal that doesn't
exist.

`driver_missing` / `driver_available` (data: `{}`) — emitted by the device watcher
(`backend/app/device/discovery.py`) when its usbmux poll starts/stops failing with
`ConnectionFailedToUsbmuxdError`, i.e. Apple Mobile Device Support isn't installed on
this machine at all (not just "no device connected"). Each fires once per state
transition, not on every poll. The UI copy/download links live entirely on the
frontend (`src/ui.js`) since the payload carries no message — see
`project_specification.md` §9 open question 2 for why this isn't bundled into the
installer instead.

`verification_complete` (data: `{verified_count, total_count, item_ids}`) — emitted by
`backend/app/ipc/handlers.py::_run_transfer_then_verify`, but **only when the transfer
engine's queue actually drained** (`TransferEngine.run()` returned `OUTCOME_DRAINED`),
regardless of whether any items actually needed (re-)verifying this run.
Paused/cancelled/disconnected runs (see `OUTCOME_*` in `app/services/transfer_engine.py`)
deliberately do **not** emit this — a mid-transfer disconnect used to be indistinguishable
from a drained queue at this layer, which jumped the UI straight to "Free Up Space" and
reported every not-yet-attempted item as an unrecoverable failure (see
`docs/DEVELOPMENT.md`). `verification_progress` only fires for items verified *this
run*; if a device was already fully transferred and verified in a prior session
(transfer state persists in SQLite across app restarts), zero `verification_progress`
events fire and the frontend would otherwise have no signal to leave the "Verifying…"
screen. `item_ids` is the authoritative full list of currently-verified item ids for
this device (not just ones touched this run) — the frontend uses it directly for
`delete.batch`'s `item_ids` param.

`transfer_progress`'s `device_bytes_transferred` is an authoritative, DB-sourced sum of
`bytes_transferred` across every item for the device (`TransferEngine.
_device_bytes_transferred`) — not something the frontend should reconstruct itself from
its own running total of this event stream, since that resets to 0 on every app
relaunch even though the device/DB still remembers everything already copied in
earlier sessions. `bytes_transferred`/`remote_size_bytes` remain the per-item figures.

`library_scan_progress` (data: `{scanned, total}`) / `library_scan_complete` (data:
`{scanned_count, group_count}`) — Library Cleanup module (spec §11) equivalents of
`transfer_progress`/`verification_complete`, emitted by
`backend/app/ipc/handlers.py::_run_library_scan` around `app/services/library_scan.
scan_library`. `library_scan_progress` fires every
`settings.LIBRARY_SCAN_PROGRESS_EVERY_N_FILES` files (mirrors
`DB_PROGRESS_FLUSH_EVERY_N_CHUNKS`'s reasoning — one event per file would flood the
channel on a many-thousand-file scan) and always on the last file regardless of that
interval. `library_scan_complete` fires once, unconditionally, when the scan (walk +
hash + group formation) finishes.

## Versioning

No version negotiation in MVP — Rust and Python ship from the same repo/build, so they
are always in lockstep. If that stops being true (e.g. hot-patching one side), add a
`protocol_version` handshake at that point, not before.
