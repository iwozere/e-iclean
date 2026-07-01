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

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class AfcFileInfo:
    path: str
    size: int
    modified_at: Optional[datetime] = None


class SeekNotSupportedError(Exception):
    """Raised when a resume seek is known to be unreliable for this handle/file."""


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

    def list_directory(self, remote_dir: str) -> list[AfcFileInfo]:
        prefix = remote_dir.rstrip("/") + "/"
        out = []
        for path, data in self._files.items():
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "/" in rest:
                continue  # lives in a deeper subdirectory
            out.append(AfcFileInfo(path=path, size=len(data)))
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
        return AfcFileInfo(path=remote_path, size=len(data))

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
    """Real AFC client backed by pymobiledevice3.

    API surface (lockdown/AfcService/listdir/stat/open/rm) matches pymobiledevice3 as
    of the version pinned in backend/requirements.txt at the time this was written —
    confirm against the installed version before relying on it (spec §9).
    """

    def __init__(self, udid: str) -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService

        self._lockdown = create_using_usbmux(serial=udid)
        self._afc = AfcService(self._lockdown)

    def list_directory(self, remote_dir: str) -> list[AfcFileInfo]:
        out = []
        for name in self._afc.listdir(remote_dir):
            full_path = f"{remote_dir.rstrip('/')}/{name}"
            stat = self._afc.stat(full_path)
            if stat.get("st_ifmt") == "S_IFDIR":
                continue
            out.append(AfcFileInfo(path=full_path, size=int(stat["st_size"])))
        return out

    def list_subdirectories(self, remote_dir: str) -> list[str]:
        out = []
        for name in self._afc.listdir(remote_dir):
            full_path = f"{remote_dir.rstrip('/')}/{name}"
            stat = self._afc.stat(full_path)
            if stat.get("st_ifmt") == "S_IFDIR":
                out.append(full_path)
        return out

    def file_info(self, remote_path: str) -> AfcFileInfo:
        stat = self._afc.stat(remote_path)
        return AfcFileInfo(path=remote_path, size=int(stat["st_size"]))

    def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
        with self._afc.open(remote_path, "rb") as handle:
            if offset:
                handle.seek(offset)
            return handle.read(length)

    def remove(self, remote_path: str) -> None:
        self._afc.rm(remote_path)

    def close(self) -> None:
        self._lockdown.close()
