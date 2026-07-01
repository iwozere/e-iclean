# E-FileTrans

Resumable USB photo/video transfer from iPhone to Windows, with verify-before-delete
cleanup. See `docs/project_specification.md` for the full product spec and
`docs/ipc_protocol.md` for the Rust&harr;Python contract. Contributor conventions live
in `AGENTS.md` — read it before making changes.

**Status:** MVP scaffold in progress. Backend transfer/verify/delete logic is
implemented and unit-tested against a mocked device layer; the Tauri shell and web UI
are wired end-to-end but have not yet been exercised against a real iPhone (no
hardware in this dev environment — see "Known gaps" below).

## Architecture

```
src/          vanilla JS/HTML/CSS web UI (no build step)
src-tauri/    Rust shell: window, spawns/owns the Python sidecar, IPC bridge
backend/      Python sidecar: pymobiledevice3, transfer engine, SQLite state
docs/         product spec + IPC protocol contract
```

The Rust shell and Python backend talk over newline-delimited JSON-RPC on stdio — no
HTTP, no network listener. See `docs/ipc_protocol.md`.

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

**Not yet wired**: production builds need the Python backend frozen into a
standalone executable (PyInstaller) and referenced from `src-tauri/src/sidecar.rs`
instead of the dev-mode venv path — see "Known gaps" below and spec §5.8.

## Known gaps (tracked, not silently skipped)

These are explicitly deferred, either because they need hardware/licensing decisions
this environment can't make, or because they're follow-up work beyond the initial
scaffold:

- **No real-device testing yet.** This dev environment has no physical iPhone or USB
  access. All backend logic is tested against a mocked AFC client
  (`app.device.afc_client.MockAfcClient`). The acceptance criteria in spec §5.10
  (5,000+ item libraries, physical cable-pull tests, etc.) still need to run against
  real hardware before this ships.
- **AFC seek/resume (spec §9 open question 3) — partially resolved by API
  inspection, not yet by hardware test.** Confirmed by reading pymobiledevice3
  9.32.0's source directly: `AfcService` has no `fseek` at all — `fopen`/`fread`/
  `fclose` only support one sequential cursor per handle. `PymobiledeviceAfcClient`
  (see `backend/app/device/afc_client.py`) is written around that constraint: it
  keeps one handle open across sequential chunk reads within a file, and raises
  `SeekNotSupportedError` (triggering the documented single-file-restart fallback in
  `transfer_engine.py`) whenever a requested offset doesn't match the live sequential
  position — e.g. resuming after an app restart. This logic is unverified against a
  real device/iOS version; a first real-hardware test is the next step, not a
  from-scratch investigation.
- **pymobiledevice3 API is more async than the spec assumed, now confirmed and
  fixed**: `usbmux.list_devices`, `lockdown.create_using_usbmux`, and
  `AfcService.connect/fread/fwrite/fclose/close` are coroutine functions in 9.32.0;
  `fopen/listdir/stat/rm` are not. A real `cargo tauri dev` smoke test on this
  machine caught the mismatch (an unawaited-coroutine warning and a dead device
  watcher loop) before this was fixed — see `backend/app/device/discovery.py`,
  `pairing.py`, and `afc_client.py`. Re-verify if `pymobiledevice3` is upgraded.
- **Live Photo pairing uses a filename-stem heuristic**, not Apple's actual asset
  metadata (spec §9, open question 4) — see `backend/app/services/live_photo.py`.
- **Installer / driver bundling not started** (spec §5.8, §9 open question 2):
  PyInstaller freezing, NSIS/WiX packaging, and the Apple Mobile Device Support
  bundle-vs-prompt decision all need to happen before a shippable installer exists.
- **Native folder picker and "open in Explorer" are stubbed** in `src/main.js`
  (currently a text prompt / alert) pending the Tauri `dialog` and `opener` plugins.
- **Settings persistence is a no-op** beyond process defaults
  (`backend/app/ipc/handlers.py::handle_settings_set`) — needs a small key/value table.
- **Destination free-space warning** (spec §5.7 point 3) is not yet implemented.

## Toolchain notes

- Rust/Cargo were installed to `D:\tools` (not the default `%USERPROFILE%\.cargo`)
  per the project owner's preference — see AGENTS.md §13.
- MSVC Build Tools and WebView2 were already present on this machine; no additional
  install was needed for those.
