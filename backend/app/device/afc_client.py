"""AFC (Apple File Conduit) client abstraction.

This module (and its siblings discovery.py / pairing.py) is the *only* place that may
import `pymobiledevice3` directly — everything in app/services/ depends on the
`AfcClient` protocol so it stays testable without real hardware (AGENTS.md §9).

NOTE on seek/resume (spec docs/project_specification.md §9 open question 3): whether
pymobiledevice3's AFC file handles reliably support seeking to an arbitrary offset for
resume, across iOS versions and file types, has not been empirically verified yet. The
real implementation below assumes `seek()` works on a freshly-opened handle; if that
assumption proves false for some device/file combination, `read_chunk` should raise
`SeekNotSupportedError` instead of returning corrupt data, so `transfer_engine` can fall
back to restarting that single file (never the whole queue). Verify this against a real
device before relying on it for the acceptance criteria in spec §5.10.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, cast

from app.config import settings
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)


@dataclass
class AfcFileInfo:
    path: str
    size: int
    modified_at: Optional[datetime] = None


class SeekNotSupportedError(Exception):
    """Raised when a resume seek is known to be unreliable for this handle/file."""


class AfcConnectionLostError(Exception):
    """Raised only when a read fails in a way that looks like the underlying AFC/
    lockdown session itself died (transport-level failure, retried and never
    recovered) - as opposed to the device responding normally with a per-file error
    status (e.g. "file not found"), which is that one item's problem, not the whole
    queue's. Confirmed against a real ~141GB/12k-item transfer: a single missing/
    inaccessible file (pymobiledevice3's AfcFileNotFoundError - the device answered
    just fine, with an error) was being misclassified as a full disconnect, pausing
    the entire transfer and showing "Connection lost" for something that wasn't a
    connection problem at all. Only this exception should map to DEVICE_DISCONNECTED
    in transfer_engine.py; anything else is a normal per-item failure."""


class AfcClient(Protocol):
    """Everything above app/device/ talks to this interface, never to pymobiledevice3."""

    def list_directory(self, remote_dir: str) -> list[AfcFileInfo]:
        """Return files (not subdirectories) immediately under `remote_dir`."""
        ...

    def list_subdirectories(self, remote_dir: str) -> list[str]:
        """Return full paths of subdirectories immediately under `remote_dir`."""
        ...

    def file_info(self, remote_path: str) -> AfcFileInfo:
        """Return size/metadata for a single remote file."""
        ...

    def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
        """Read `length` bytes starting at `offset`. May raise SeekNotSupportedError."""
        ...

    def remove(self, remote_path: str) -> None:
        """Delete a single remote file. Caller guarantees it has been verified."""

    def close(self) -> None:
        """Release the underlying device session."""


class MockAfcClient:
    """In-memory AFC client for tests and hardware-less development.

    Construct with a dict of {remote_path: bytes}. Directory structure is inferred
    from path prefixes, mirroring the real iPhone layout (/DCIM/100APPLE/IMG_0001.HEIC).
    """

    def __init__(self, files: Optional[dict[str, bytes]] = None) -> None:
        self._files: dict[str, bytes] = dict(files or {})
        self.unseekable_paths: set[str] = set()
        # Test-only knob (mirrors unseekable_paths above): lets tests simulate a
        # device reporting a modified date for a given remote_path, without needing
        # real AFC stat() plumbing.
        self.modified_at: dict[str, datetime] = {}

    def list_directory(self, remote_dir: str) -> list[AfcFileInfo]:
        prefix = remote_dir.rstrip("/") + "/"
        out = []
        for path, data in self._files.items():
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "/" in rest:
                continue  # lives in a deeper subdirectory
            out.append(AfcFileInfo(path=path, size=len(data), modified_at=self.modified_at.get(path)))
        return out

    def list_subdirectories(self, remote_dir: str) -> list[str]:
        prefix = remote_dir.rstrip("/") + "/"
        dirs = set()
        for path in self._files:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "/" in rest:
                dirs.add(prefix + rest.split("/", 1)[0])
        return sorted(dirs)

    def file_info(self, remote_path: str) -> AfcFileInfo:
        data = self._get_or_raise(remote_path)
        return AfcFileInfo(path=remote_path, size=len(data), modified_at=self.modified_at.get(remote_path))

    def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
        if remote_path in self.unseekable_paths and offset > 0:
            raise SeekNotSupportedError(remote_path)
        data = self._get_or_raise(remote_path)
        return data[offset : offset + length]

    def remove(self, remote_path: str) -> None:
        self._files.pop(remote_path, None)

    def close(self) -> None:
        pass

    def _get_or_raise(self, remote_path: str) -> bytes:
        data = self._files.get(remote_path)
        if data is None:
            raise FileNotFoundError(remote_path)
        return data


class PymobiledeviceAfcClient:
    """Real AFC client backed by pymobiledevice3 (version pinned in
    backend/requirements.txt; API surface below was confirmed empirically against the
    installed 9.32.0 at the time this was written - re-verify on upgrade, spec §9.1).

    IMPORTANT (spec §9 open question 3, confirmed empirically): this AFC surface has
    no fseek. `fopen`/`fread`/`fclose` operate over a single sequential read cursor
    per handle. Resume is therefore only possible by keeping one handle open across
    sequential chunk reads within a single file transfer; a read requested at an
    offset that doesn't match the currently-open sequential position (e.g. because the
    app restarted mid-file) cannot seek there and must raise `SeekNotSupportedError`
    so the caller falls back to restarting that single file from byte 0
    (see app/services/transfer_engine.py).

    IMPORTANT (confirmed empirically against a real device): every `AfcService` call
    is a coroutine (see the class docstring history in docs/DEVELOPMENT.md for the
    `@path_to_str()` decorator gotcha that hid this for `listdir`/`stat`/`fopen`/`rm`).
    An earlier version of this class ran each call through its own `asyncio.run(...)`,
    which appeared to work but broke the connection after a handful of calls
    (`ConnectionResetError: Connection lost`) - `asyncio.run()` tears the event loop
    down when the coroutine returns, and the AFC connection's stream reader/writer are
    bound to whichever loop created them in `__init__`. Every subsequent call from a
    *different*, short-lived loop is operating on a transport whose owning loop no
    longer exists. The fix: one background thread runs a single persistent event loop
    for this client's entire lifetime; every AFC call is submitted to *that* loop via
    `asyncio.run_coroutine_threadsafe` and blocks the calling (sync) method for the
    result, keeping `AfcClient`'s synchronous interface without repeatedly tearing
    down the connection's loop.
    """

    def __init__(self, udid: str) -> None:
        import asyncio
        import threading

        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        async def _connect():
            lockdown = await create_using_usbmux(serial=udid)
            afc = AfcService(lockdown)
            await afc.connect()
            return lockdown, afc

        self._lockdown, self._afc = self._run(_connect())
        self._open_path: Optional[str] = None
        self._open_handle: Optional[int] = None
        self._open_position: int = 0

    def _run(self, coro):
        """Submit a coroutine to this client's single persistent event loop and block
        for its result - see the class docstring for why a fresh loop per call
        (`asyncio.run(...)`) breaks the connection."""
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_directory(self, remote_dir: str) -> list[AfcFileInfo]:
        out = []
        for name in self._run(self._afc.listdir(remote_dir)):
            full_path = f"{remote_dir.rstrip('/')}/{name}"
            stat = self._run(self._afc.stat(full_path))
            if stat.get("st_ifmt") == "S_IFDIR":
                continue
            # st_mtime is already a datetime (pymobiledevice3's AfcService.stat
            # converts it) - this must be threaded through for date-based
            # destination-folder nesting (transfer_engine.py) to work at all against
            # a real device; it was silently dropped here before, which would have
            # put every real file in the "unknown-date" bucket.
            out.append(AfcFileInfo(path=full_path, size=int(stat["st_size"]), modified_at=cast(Optional[datetime], stat.get("st_mtime"))))
        return out

    def list_subdirectories(self, remote_dir: str) -> list[str]:
        out = []
        for name in self._run(self._afc.listdir(remote_dir)):
            full_path = f"{remote_dir.rstrip('/')}/{name}"
            stat = self._run(self._afc.stat(full_path))
            if stat.get("st_ifmt") == "S_IFDIR":
                out.append(full_path)
        return out

    def file_info(self, remote_path: str) -> AfcFileInfo:
        stat = self._run(self._afc.stat(remote_path))
        return AfcFileInfo(path=remote_path, size=int(stat["st_size"]), modified_at=cast(Optional[datetime], stat.get("st_mtime")))

    def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
        # A single fread (or the fopen preceding it) can fail transiently on a session
        # that's otherwise still alive - confirmed against a real ~141GB/12k-item
        # transfer, where the device never left usbmux at all. Retrying here, forcing
        # a fresh fopen each attempt, resolves those without ever surfacing
        # "connection lost" to the user.
        #
        # AfcException (and subclasses, e.g. AfcFileNotFoundError) means the AFC
        # service answered normally, just with an error status for this one file -
        # confirmed live against a real device: status 8 (object not found) on a
        # single HEIC out of 12174 items. That's not a connection problem, retrying
        # the identical fopen won't change the outcome, and it must NOT be classified
        # as AfcConnectionLostError below - doing so previously paused the *entire*
        # transfer and showed a misleading "Connection lost" banner for one bad file.
        # Let it propagate as-is; transfer_engine treats it as a normal per-item
        # failure and moves on to the next item.
        from pymobiledevice3.exceptions import AfcException

        last_exc: Optional[Exception] = None
        for attempt in range(settings.AFC_READ_RETRY_ATTEMPTS):
            try:
                if attempt > 0 or self._open_path != remote_path or offset != self._open_position:
                    if offset != 0:
                        raise SeekNotSupportedError(remote_path)
                    self._close_open_handle()
                    self._open_handle = self._run(self._afc.fopen(remote_path, "r"))
                    self._open_path = remote_path
                    self._open_position = 0

                assert self._open_handle is not None
                chunk = self._run(self._afc.fread(self._open_handle, length))
                self._open_position += len(chunk)
                if not chunk:
                    self._close_open_handle()
                return chunk
            except SeekNotSupportedError:
                raise
            except AfcException:
                self._close_open_handle()
                raise
            except Exception as exc:  # pylint: disable=broad-except
                last_exc = exc
                _logger.warning(
                    "afc_client: read_chunk failed remote_path=%s attempt=%s/%s",
                    remote_path,
                    attempt + 1,
                    settings.AFC_READ_RETRY_ATTEMPTS,
                    exc_info=True,
                )
                self._close_open_handle()
                if attempt + 1 < settings.AFC_READ_RETRY_ATTEMPTS:
                    time.sleep(settings.AFC_READ_RETRY_DELAY_SECONDS)

        assert last_exc is not None
        raise AfcConnectionLostError(str(last_exc)) from last_exc

    def _close_open_handle(self) -> None:
        if self._open_handle is not None:
            try:
                self._run(self._afc.fclose(self._open_handle))
            except Exception:  # pylint: disable=broad-except
                pass
        self._open_handle = None
        self._open_path = None
        self._open_position = 0

    def remove(self, remote_path: str) -> None:
        self._run(self._afc.rm(remote_path))

    def close(self) -> None:
        async def _close():
            await self._afc.close()
            await self._lockdown.close()

        self._close_open_handle()
        try:
            self._run(_close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
            self._loop.close()
