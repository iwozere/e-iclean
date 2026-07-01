# Agent & Contributor Guide — E-FileTrans (e-iclean)

This document describes the conventions and working agreements for **E-FileTrans**, a
local-only Windows app that copies photos/videos from an iPhone to a local folder with
resumable transfer and verify-before-delete semantics. It is binding for both human
developers and AI agents contributing to the codebase. It is the single source of truth
for style, repo layout, tooling, and the run/test/build workflow.

The full product spec lives at `docs/project_specification.md` — read it before making
any scope decisions. This file is about *how* to build it, not *what* to build.

---

## 0. Project Overview

E-FileTrans is a **two-process desktop app**:

| Path | Stack | Purpose |
|------|-------|---------|
| `src-tauri/` | **Rust**, Tauri 2 | App shell: window, process lifecycle, spawns/owns the Python sidecar, bridges IPC to the webview, native OS calls (power management, file dialogs). |
| `src/` | **Vanilla JS** (no build step), HTML, CSS | Web player UI — connect/transfer/verify/delete screens. |
| `backend/` | Python 3.13 | Sidecar process: `pymobiledevice3` device comms, transfer engine (queue/resume), SQLite state, verification, deletion. Talks to the Rust core over stdio JSON-RPC — **never serves HTTP**, has no network listener. |

Runtime context:

- **Windows 10 (21H2+) / Windows 11 only** for v1. No macOS, no Linux.
- The Python sidecar is bundled as a frozen executable (PyInstaller) inside the final
  installer — end users never see a Python environment.
- iPhone communication is **USB only** for v1, via `pymobiledevice3` (usbmux, lockdownd,
  AFC). No software is installed on the iPhone beyond the stock "Trust This Computer"
  prompt.
- Persistence is **SQLite**, one file per install at `%APPDATA%/EFileTrans/state.db`.
- This is a **fully local, no-telemetry product**. Never add a network call that isn't
  local device communication without flagging it to the user first.

When making any change, keep the resource targets in mind: idle memory under 150 MB, no
GPU requirement, UI must stay responsive during transfer (all device I/O off the UI
thread / off the Rust main thread).

---

## 1. General Style

### 1.1 Rust (`src-tauri/`)

- Follow standard `rustfmt` formatting (`cargo fmt` before committing) and
  `cargo clippy -- -D warnings` must pass on touched crates.
- Prefer `Result<T, E>` with a project-local error enum over `unwrap()`/`expect()`
  outside of tests and `main()`. A panicking shell crashes the whole app, including the
  in-flight transfer.
- Keep `src-tauri/src/main.rs` thin: wiring only. Real logic (process management, IPC
  framing, command handlers) lives in dedicated modules under `src-tauri/src/`.

### 1.2 Python (`backend/`)

- Follow **[PEP 8](https://peps.python.org/pep-0008/)** unless explicitly overridden below.
- **4 spaces** per indent level; **UTF-8** source files.
- Maximum line length: **120 characters**.
- One public class or function per file, where practical.
- Always use the project **`.venv`** for Python work (located at `backend/.venv/`).
- If you find diagnostics / linter issues in code you touch, fix them immediately.
- Prefer clear, maintainable code over clever one-liners.
- The backend has **no HTTP framework**. Do not introduce FastAPI/Flask/etc. — the
  sidecar speaks JSON-RPC over stdio only (see `docs/ipc_protocol.md`).

### 1.3 Web UI Versioning (cache busting)

When modifying any file in `src/`, you **MUST**:

1. Bump the version string shown in the UI (settings screen footer).
2. Bump the version in the `console.log` at the top of `main.js`.
3. Bump the `?v=X.X.X` query parameter on **every** `<script>` and `<link>` tag in
   `index.html` so the WebView doesn't serve a stale cached asset.

Also bump the version in `README.md` when shipping a user-visible change.

---

## 2. Imports

### Python

- Use **absolute imports** rooted at the `app` package (the backend runs with
  `pythonpath = .` from `backend/`).
- Place imports at the top of the file, grouped and blank-line separated:
  1. Standard library
  2. Third-party packages (`pymobiledevice3`, `pydantic`, ...)
  3. Local application (`app.*`)

```python
import os
import time
from typing import Optional

from pydantic import BaseModel

from app.config import settings
from app.services import transfer_engine
from app.utils.logger import setup_logger
```

#### `__init__.py` files

Keep all `__init__.py` files empty unless re-exporting is genuinely necessary.

#### UTC-aware dates

```python
# Do NOT
datetime.now(datetime.UTC)
# Do
from datetime import datetime, timezone
datetime.now(timezone.utc)
```

### Rust

- Group `use` statements: std, external crates, then `crate::*`, each block
  alphabetized (`rustfmt` handles this — don't fight it).

---

## 3. Logging

### 3.1 Python initialization

Every module initializes its logger the same way:

```python
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)
```

Logs write to a local rotating file at `%APPDATA%/EFileTrans/logs/`. **Never** transmit
logs anywhere — this product has no telemetry in MVP (§5.9, §7 of the spec).

### 3.2 Lazy formatting (always)

```python
# Do NOT
_logger.info(f"Transferring {file_name}")
# Do
_logger.info("Transferring %s", file_name)
```

### 3.3 Levels

- `debug()` — detailed debugging information (chunk-level transfer detail).
- `info()` — high-level runtime events (session start/end, file completed).
- `warning()` — unexpected but non-fatal events (resumed after disconnect).
- `error()` — serious problems needing attention.
- `exception()` — like `error()` with stack trace; use **only inside `except`**.

### 3.4 Observability conventions

Hot-path code (transfer engine, device watcher) emits structured, greppable log lines:
a stable prefix, `key=value` fields, and millisecond timings — e.g.
`transfer_engine: chunk written item_id=... bytes=... elapsed_ms=...`,
`device_watcher: disconnect detected udid=... gap_ms=...`,
`afc_client: resume seek item_id=... offset=...`.
Never log full local file paths or photo filenames at `info` or above if they can be
avoided — prefer item IDs; this data is locally-sensitive per §7 of the spec.

### 3.5 Rust

Use the `log`/`tracing` crate with the same level discipline as above. Forward
backend-originated log lines verbatim (don't re-interpret them) when surfacing to the
app's combined log file.

---

## 4. Naming Conventions

- **Python modules & packages**: `lowercase_with_underscores`
- **Python classes**: `CamelCase`
- **Python functions & variables**: `lowercase_with_underscores`
- **Python constants**: `UPPERCASE_WITH_UNDERSCORES`
- **Private members**: prefix with `_`
- **Config settings**: `UPPERCASE_WITH_UNDERSCORES` on the `Settings` model in
  `backend/app/config.py`.
- **Rust**: `snake_case` for functions/modules/variables, `CamelCase` for
  types/traits/enums, `SCREAMING_SNAKE_CASE` for constants (standard Rust convention).
- **JS**: `camelCase` for functions/variables, `PascalCase` for any constructor-style
  helpers, `UPPER_SNAKE_CASE` for module-level constants.
- **Transfer item status values**: always one of the fixed set from
  `docs/project_specification.md` §5.3 (`pending | in_progress | partial | copied |
  verified | delete_pending | deleted | failed`) — never invent ad-hoc status strings.

---

## 5. Docstrings

- Python: follow **[PEP 257](https://peps.python.org/pep-0257/)**; triple double quotes.
  First line: short summary; blank line before any detailed description.

```python
def add(a: int, b: int) -> int:
    """
    Add two integers.

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        The sum of `a` and `b`.
    """
    return a + b
```

- Rust: `///` doc comments on public items; keep them short, expand only when behavior
  is non-obvious (e.g. resume/seek fallback semantics).

---

## 6. Type Hints

- Python: type-hint all function arguments and return values. Use `Optional[...]` for
  nullable values; prefer precise types over `Any`. IPC payloads are Pydantic models in
  `app/schemas.py`, not raw dicts, once they cross a module boundary.
- Rust: lean on the type system for IPC framing (serde-derived structs matching the
  Pydantic schemas) rather than passing around untyped `serde_json::Value` past the
  parsing boundary.

---

## 7. Error Handling

- Never use a bare `except:`. Catch specific exceptions.
- A broad `except Exception` is acceptable only at defensive boundaries (the device
  watcher loop, the transfer worker loop) and must be paired with a logged reason and a
  comment explaining why the boundary is broad.
- Log exceptions with `_logger.exception("... %s", context)` inside the `except`.
- Map known error classes (pairing failure, AFC timeout, disk full, permission denied,
  device disconnected mid-read) to **plain-language messages** before they reach the UI
  — see `app/utils/errors.py`. Raw protocol/exception text must never reach the user
  (§5.9 of the spec). New failure modes get a new mapped error, not a generic fallback
  string, unless truly unanticipated.
- On the Rust side, IPC failures and sidecar crashes must surface as a UI-visible error
  state, never a silent hang — the UI should never sit in a spinner with no timeout.

---

## 8. Code Structure

- Keep functions short and focused; prefer early returns over deep nesting.
- Extract helpers to avoid duplication.
- Keep blocking work (subprocess calls, AFC reads, hashing) off any event loop / off the
  Rust async runtime's executor threads — use `asyncio.to_thread(...)` on the Python
  side, and `tauri::async_runtime::spawn_blocking` (or a dedicated worker thread) on the
  Rust side for anything that touches the sidecar pipe or a native dialog.

---

## 9. Backend Architecture (Python sidecar)

The backend lives under `backend/`:

```
backend/
  run.py                  # local entrypoint — starts the stdio JSON-RPC loop
  pytest.ini              # pythonpath=., asyncio_mode=auto, testpaths=app/tests
  requirements.txt
  app/
    main.py               # stdio read/dispatch loop, lifespan (DB init, watcher startup)
    config.py             # Settings (pydantic-settings); all tunables live here
    models.py             # SQLModel ORM models — mirrors spec §5.3 exactly
    schemas.py             # Pydantic IPC request/response/event schemas
    db.py                   # SQLite engine / session
    ipc/                     # JSON-RPC framing + method dispatch (see docs/ipc_protocol.md)
    device/                   # pymobiledevice3-facing code: discovery, pairing, AFC client
      afc_client.py             # AfcClient protocol/interface + real + mock implementations
      discovery.py                # usbmux device watcher loop
      pairing.py                    # trust/pairing flow
    services/                       # business logic, one concern per module
      enumeration.py                  # walk /DCIM, build/refresh the transfer manifest
      transfer_engine.py                # queue, chunked read/write, resume, reconnection
      verification.py                     # size + SHA-256 checksum verification
      live_photo.py                         # pair HEIC/JPG with its .MOV
      delete_service.py                       # batched AFC delete, verified-only guard
      usb_power.py                              # USB selective-suspend mitigation
    utils/
      logger.py
      errors.py             # exception -> plain-language error code/message mapping
    tests/                   # pytest suite (test_*.py)
```

Rules:

- **`device/`**: the *only* place `pymobiledevice3` is imported directly. Everything
  above it talks to the `AfcClient`/`DeviceWatcher` interfaces, never the library
  directly — this is what makes the transfer engine testable without real hardware
  (§9 open question 3 in the spec: AFC seek/resume support is still empirically
  unconfirmed, so the interface must make swapping the seek strategy a one-place change).
- **`services/`**: business logic. New cross-cutting logic goes here, not in `main.py`
  or `ipc/`.
- **Config** (`app/config.py`): add a typed setting with a sane default for every new
  tunable (chunk size, concurrency, poll interval, trust-prompt timeout, etc.). Do not
  hard-code magic numbers in `transfer_engine.py` or `discovery.py`.
- **Migrations**: for MVP, schema is created directly from `models.py` at first run
  (no prior schema to migrate from). If the schema changes after the first release,
  introduce Alembic at that point — don't add migration machinery before there's
  anything to migrate.

---

## 10. Rust Shell Architecture (`src-tauri/`)

```
src-tauri/
  Cargo.toml
  tauri.conf.json
  src/
    main.rs              # app entrypoint, window setup, wires commands + sidecar
    sidecar.rs            # spawns/owns the Python sidecar process, stdio plumbing
    ipc.rs                  # JSON-RPC request/response framing, pending-request table
    commands.rs                # #[tauri::command] handlers exposed to the webview
    events.rs                    # typed wrappers for emitting sidecar notifications to JS
    power.rs                       # USB selective-suspend toggle (Windows power APIs)
```

Rules:

- `commands.rs` functions are thin: validate input, forward to `ipc.rs`, return/await
  the response. No business logic here — that lives in the Python backend.
- Every JSON-RPC notification from the sidecar (`device_connected`,
  `transfer_progress`, `connection_lost`, etc.) gets a typed event emitted via
  `events.rs` — the JS side never parses raw JSON-RPC.
- If the sidecar process dies unexpectedly, `sidecar.rs` must emit a `backend_crashed`
  event (not just log it) so the UI can show a real error state instead of hanging.

---

## 11. Frontend (`src/`, vanilla JS)

- No build step, no bundler, no framework — matches the simplicity of the rest of the
  stack and keeps the WebView footprint small.
- One file per concern: `api.js` (Tauri `invoke`/event-listen wrappers), `ui.js`
  (DOM rendering for the screens in spec §5.7), `main.js` (wiring/bootstrap).
- UI states mirror spec §5.5 exactly (`disconnected`, `awaiting_trust`, `enumerating`,
  `ready`, `transferring`, `connection_lost`, `verifying`, `ready_to_clean`, `deleting`,
  `done`) — render as an explicit state machine, not ad-hoc flag combinations.
- Never let the UI assume an action succeeded without an explicit response/event from
  the backend — every button press that triggers backend work shows a pending state.

---

## 12. Tests

### Python

- All new backend code must include unit tests under `backend/app/tests/`.
- Test function names: `test_<functionality>` (e.g.
  `test_resume_seeks_to_last_confirmed_offset`).
- Run the suite from the `backend/` directory:

  ```bash
  cd backend
  .venv/Scripts/python.exe -m pytest -q     # Windows dev
  python -m pytest -q                       # CI / other platforms
  ```

- `asyncio_mode = auto`, so `async def test_...` works without extra decorators.
- Tests must **not** require a real iPhone or spawn real `pymobiledevice3` device I/O —
  mock `AfcClient`/`DeviceWatcher` at the `device/` boundary (see §9). This project has
  no CI hardware, so untestable-without-a-device code paths must be designed away, not
  skipped.

### Rust

- `cargo test` from `src-tauri/` for any pure logic extracted out of `main.rs` (IPC
  framing, event mapping). Don't chase coverage on Tauri's own plumbing.

### Temporary / throwaway scripts

- Put one-off repro or debugging scripts in the session **scratchpad**, not in the repo.
- If a temporary script must live in the tree, place it under `backend/app/tests/` and
  prefix it with `tmp_` (e.g. `tmp_repro_resume.py`); never commit `tmp_*` files.
- Do **not** use the system `/tmp` directory.

---

## 13. Running & Building

- **Local dev**: `cargo tauri dev` from the repo root (after `cd backend && pip install
  -r requirements.txt` once). Tauri's dev config spawns `backend/run.py` via the
  system Python during development; the frozen executable is only used in the packaged
  build.
- **Backend alone** (for fast iteration without the Rust shell): `cd backend && python
  run.py` — it will just sit reading stdio, so pair it with the test suite rather than
  expecting interactive output.
- **Production build**: `cargo tauri build` — this must only be run after the backend
  has been frozen with PyInstaller into `backend/dist/` per the build script (added once
  the installer task starts; see `docs/project_specification.md` §5.8).
- **Toolchain location on this machine**: Rust/Cargo live under `D:\tools\cargo` and
  `D:\tools\rustup` (`CARGO_HOME`/`RUSTUP_HOME`), already on `PATH`. Don't assume the
  default `%USERPROFILE%\.cargo` location.

---

## 14. Git & Commit Messages

- Imperative mood: `"Add chunked resume to transfer engine"`, not `"Added ..."`.
- Reference issues when applicable: `"Fix #123 - handle 416 as clean EOF"`.
- Keep commits focused; don't mix a feature with unrelated reformatting.
- Commit or push only when asked; if on `main`, branch first for anything risky —
  however, during the initial scaffold-and-build phase, incremental commits directly
  documenting MVP progress are expected and welcome.

---

## 15. AI Agent Guidelines (binding)

1. **Read this file and `docs/project_specification.md`** before generating or
   modifying code. Treat spec §5 (MVP) as the implementation target and §8 (Roadmap) as
   context only — do not build roadmap features early.
2. Apply PEP 8 (Python) / `rustfmt` + `clippy` (Rust) plus every custom rule above.
3. Use the standard Python **logger init** and **lazy logging** format; never log
   secrets, full file paths, or photo filenames above `debug`.
4. **Always** include type hints and PEP 257 docstrings on new Python functions.
5. Keep `pymobiledevice3` imports confined to `backend/app/device/`; everything else
   depends on the `AfcClient`/`DeviceWatcher` interface so it stays testable without
   hardware.
6. Add a typed, env/config-overridable **setting** (in `app/config.py`) for any new
   tunable; no magic numbers in hot paths.
7. Write **unit tests** for new backend functionality under `backend/app/tests/`,
   mocking the device boundary — this project cannot run hardware-in-the-loop tests.
8. When touching `src/`, follow the **Web UI Versioning** rules (§1.3 / §1 here).
9. Delete-from-device is **never** automatic — verify-before-delete (spec §5.6, §7) is a
   hard safety requirement, not a nice-to-have. Any code path that calls the AFC
   `remove` operation must be reachable only from an explicit, user-confirmed action.
10. Flag the open questions in spec §9 (AFC seek/resume support, Live Photo pairing
    reliability, Apple Mobile Device Support redistribution terms, iOS version drift,
    large-library enumeration performance) back to the user rather than silently
    assuming an answer — design the affected code so the assumption is a single,
    clearly-marked switch point, not baked in everywhere.
11. Keep `README.md` and this file consistent when conventions or structure change.

---

*Last updated: 2026-07-01*
