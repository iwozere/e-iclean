"""Stdio JSON-RPC loop — the sidecar's entrypoint (docs/ipc_protocol.md).

Reads newline-delimited JSON requests from stdin, dispatches them, and writes
responses/notifications to stdout. Also owns the device watcher loop and the USB
power-management mitigation for the process lifetime.
"""
import asyncio
import sys
from typing import Any

from app.db import init_db
from app.device.discovery import DeviceWatcher
from app.ipc import handlers  # pyright: ignore[reportUnusedImport]  (registers IPC methods)
from app.ipc.dispatcher import dispatch
from app.ipc.protocol import decode_message, encode_message, make_error_response, make_notification, make_response
from app.services.usb_power import disable_usb_selective_suspend, restore_usb_selective_suspend
from app.utils.errors import BACKEND_INTERNAL, AppError, app_error
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

_stdout_lock = asyncio.Lock()


async def _write_message(message: dict[str, Any]) -> None:
    line = encode_message(message)
    async with _stdout_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


async def emit_event(event: str, data: Any) -> None:
    """Send an unsolicited notification to the Rust shell."""
    payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    await _write_message(make_notification(event, payload))


async def _on_device_event(event: str, data: Any) -> None:
    await emit_event(event, data)
    if event == "device_connected":
        from app.ipc.handlers import resume_session_if_paused

        udid = data.udid if hasattr(data, "udid") else data.get("udid")
        await resume_session_if_paused(udid)


async def _handle_line(line: str) -> None:
    try:
        message = decode_message(line)
    except ValueError:
        _logger.warning("ipc: received malformed line, ignoring")
        return

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if request_id is None or method is None:
        _logger.warning("ipc: received message missing id/method, ignoring")
        return

    try:
        result = await dispatch(method, params)
        await _write_message(make_response(request_id, result))
    except AppError as exc:
        _logger.warning("ipc: method=%s failed code=%s", method, exc.code)
        await _write_message(make_error_response(request_id, exc.to_dict()))
    except Exception as exc:  # pylint: disable=broad-except
        _logger.exception("ipc: unhandled exception in method=%s", method)
        await _write_message(
            make_error_response(request_id, app_error(BACKEND_INTERNAL, detail=str(exc)).to_dict())
        )


async def _read_lines():
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        stripped = line.strip()
        if stripped:
            yield stripped


async def run_stdio_loop() -> None:
    """Initialize the DB and device watcher, then read+dispatch requests forever."""
    init_db()
    _logger.info("backend: starting stdio loop")

    from app.ipc.handlers import configure as configure_handlers

    configure_handlers(emit_event)

    watcher = DeviceWatcher(on_event=_on_device_event)
    watcher_task = asyncio.create_task(watcher.run())

    disable_usb_selective_suspend()
    try:
        async for line in _read_lines():
            asyncio.create_task(_handle_line(line))
    finally:
        watcher_task.cancel()
        restore_usb_selective_suspend()
        _logger.info("backend: stdio loop ended")
