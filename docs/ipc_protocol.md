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
{"event": "transfer_progress", "data": {"item_id": 17, "bytes_transferred": 4194304, "remote_size_bytes": 8388608}}
```

- No `id`. Not a reply to anything. Rust forwards each as a typed Tauri event
  (`events.rs`) to the webview, named identically to `event`.

## Methods (initial set — extend as MVP work proceeds, keep this list current)

| Method | Params | Result | Notes |
|---|---|---|---|
| `device.list` | `{}` | `{devices: [...]}` | Current usbmux-visible devices. |
| `device.connect` | `{udid}` | `{status, device}` | Begins pairing/trust flow if needed. |
| `library.enumerate` | `{udid}` | `{total_items, total_bytes}` | Builds/refreshes the transfer manifest in SQLite. |
| `transfer.start` | `{udid, destination}` | `{session_id}` | Starts/resumes the queue. |
| `transfer.pause` | `{session_id}` | `{}` | Pauses without losing state. |
| `transfer.cancel` | `{session_id}` | `{}` | Cancels; partial files remain resumable later. |
| `verify.status` | `{session_id}` | `{verified_count, pending_count}` | Poll-or-event hybrid; events preferred. |
| `delete.batch` | `{item_ids: [...]}` | `{deleted_count, failures: [...]}` | Only ever called from an explicit user confirmation in the UI. |
| `settings.get` / `settings.set` | varies | varies | Destination folder default, concurrency, log location. |

## Events (initial set)

`device_connected`, `device_disconnected`, `awaiting_trust`, `enumeration_progress`,
`transfer_progress`, `connection_lost`, `connection_resumed`, `verification_progress`,
`delete_progress`, `backend_crashed` (Rust-originated, sidecar process died), `error`
(out-of-band error not tied to a specific request, e.g. a background watcher failure).

## Versioning

No version negotiation in MVP — Rust and Python ship from the same repo/build, so they
are always in lockstep. If that stops being true (e.g. hot-patching one side), add a
`protocol_version` handshake at that point, not before.
