"""Sequential transfer queue with chunked resume (spec §5.4).

Concurrency > 1 is explicitly experimental per the spec and not implemented in MVP —
the queue processes one item at a time (settings.TRANSFER_CONCURRENCY exists for the
future toggle, see spec §5.7 settings screen, but is not yet wired to multiple workers).
"""
import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlalchemy import func
from sqlmodel import select

from app.config import settings
from app.db import get_session
from app.device.afc_client import AfcClient, AfcConnectionLostError, SeekNotSupportedError
from app.models import (
    STATUS_COPIED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PARTIAL,
    STATUS_PENDING,
    TransferItem,
)
from app.schemas import TransferProgressEvent
from app.utils.errors import DEVICE_DISCONNECTED, AppError, app_error
from app.utils.logger import setup_logger
from app.utils.naming import disambiguated_name

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]

# run() outcomes - the caller (handlers._run_transfer_then_verify) must only treat
# DRAINED as "the queue is actually finished" and run verification/emit
# verification_complete for that case. PAUSED/CANCELLED/DISCONNECTED all mean items
# are still sitting pending/partial in the DB and the run was cut short - see
# docs/DEVELOPMENT.md for the bug this fixes (a mid-transfer disconnect was
# indistinguishable from a drained queue, so the UI jumped straight to "Free Up
# Space" and reported thousands of never-attempted items as unrecoverable failures).
OUTCOME_DRAINED = "drained"
OUTCOME_PAUSED = "paused"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_DISCONNECTED = "disconnected"
# A reconnect during a still-in-flight run() (the old, dying client's read retries
# hadn't finished failing yet when the fresh device_connected arrived) used to spawn a
# second, fully concurrent run() on the same engine via resume_session_if_paused -
# confirmed live against a real device: a brief real USB blip mid-transfer produced
# two run() loops racing on the same item queue for a ~700ms window before the stale
# one died. It happened to self-heal (the stale loop's own in-flight item was already
# `in_progress` and so excluded from the other loop's selection), but nothing actually
# prevented true concurrent execution - TRANSFER_CONCURRENCY is explicitly 1 (see
# module docstring). run() now serializes via a lock instead of relying on timing: a
# second call queues up and starts only once the first has fully exited, so the
# reconnect-triggered resume still auto-continues the queue (as observed live) but
# never runs alongside the run() it's replacing.


class TransferEngine:
    """Processes pending/partial items for one device into one destination folder."""

    def __init__(self, udid: str, destination: Path, afc: AfcClient, on_event: EventEmitter) -> None:
        self._udid = udid
        self._destination = destination
        self._afc = afc
        self._on_event = on_event
        self._paused = False
        self._cancelled = False
        self._run_lock = asyncio.Lock()

    def pause(self) -> None:
        """Request the queue stop after the current file (no data loss, see §5.4)."""
        self._paused = True

    def cancel(self) -> None:
        """Request the queue stop after the current file; resumable later (FR-10)."""
        self._cancelled = True

    def set_afc(self, afc: AfcClient) -> None:
        """Swap in a fresh AFC client (e.g. after a reconnect replaced the old one) so
        a resumed run() doesn't keep reading through a stale/closed connection."""
        self._afc = afc

    async def run(self) -> str:
        """Process pending/partial items in id order until done, paused, or cancelled.

        Returns an OUTCOME_* string so the caller can tell a truly drained queue apart
        from one merely interrupted by pause/cancel/disconnect. Serialized via a lock -
        see the module-level note above run() being called while a previous call on
        this same engine is still in flight (e.g. a reconnect-triggered resume racing
        the run it's replacing) queues up rather than executing concurrently.
        """
        async with self._run_lock:
            self._paused = False
            self._cancelled = False
            self._destination.mkdir(parents=True, exist_ok=True)

            while True:
                if self._cancelled:
                    _logger.info("transfer_engine: cancelled udid=%s", self._udid)
                    return OUTCOME_CANCELLED
                if self._paused:
                    _logger.info("transfer_engine: paused udid=%s", self._udid)
                    return OUTCOME_PAUSED

                item_id = self._next_item_id()
                if item_id is None:
                    _logger.info("transfer_engine: queue drained udid=%s", self._udid)
                    return OUTCOME_DRAINED

                try:
                    await self._transfer_item(item_id)
                except AppError as exc:
                    if exc.code == DEVICE_DISCONNECTED:
                        _logger.warning(
                            "transfer_engine: connection lost item_id=%s udid=%s detail=%s",
                            item_id,
                            self._udid,
                            exc.detail,
                        )
                        self._mark_partial(item_id)
                        await self._on_event("connection_lost", {"udid": self._udid})
                        return OUTCOME_DISCONNECTED
                    _logger.warning("transfer_engine: item failed item_id=%s code=%s", item_id, exc.code)
                    self._mark_failed(item_id, exc.message)
                except Exception:  # pylint: disable=broad-except
                    _logger.exception("transfer_engine: unexpected error item_id=%s", item_id)
                    self._mark_failed(item_id, app_error("backend_internal").message)

    def _target_subdir(self, remote_modified_at) -> str:
        """Year-month bucket a file lands in, e.g. "2026-07". Items with no known
        modified date (AFC didn't report one) fall into a single "unknown-date"
        bucket rather than the destination root."""
        if remote_modified_at is None:
            return "unknown-date"
        return f"{remote_modified_at.year:04d}-{remote_modified_at.month:02d}"

    def _local_relative_path(self, item_id: int, file_name: str, remote_path: str, remote_modified_at) -> Path:
        """Where this item lands under the destination folder: `YYYY-MM/file_name`.

        Date-based nesting (rather than a flat destination, or mirroring Apple's raw
        DCIM/NNNAPPLE numbering) mostly avoids filename collisions by construction -
        different photos rarely share both a filename *and* a capture month - while
        staying human-browsable, unlike opaque "104APPLE"-style folder names.

        It doesn't *guarantee* uniqueness on its own though: two different remote
        files can still share both a filename and a month (confirmed as a real
        failure mode against a ~12k-item library before this had any disambiguation
        at all - see docs/DEVELOPMENT.md). So this keeps the same fallback as before:
        if another item for this device would land in the *same* subdir with the
        *same* file_name, fold that item's DCIM parent folder name into the filename.
        remote_path is unique per device (DB constraint) and the bucket is
        deterministic from remote_modified_at, so a later resume/retry always
        recomputes the identical path.
        """
        subdir = self._target_subdir(remote_modified_at)
        with get_session() as session:
            same_name = session.exec(
                select(TransferItem).where(
                    TransferItem.device_udid == self._udid,
                    TransferItem.file_name == file_name,
                    TransferItem.id != item_id,
                )
            ).all()
        collides = any(self._target_subdir(other.remote_modified_at) == subdir for other in same_name)
        if not collides:
            return Path(subdir) / file_name
        return Path(subdir) / disambiguated_name(file_name, remote_path)

    def _next_item_id(self) -> Optional[int]:
        with get_session() as session:
            item = session.exec(
                select(TransferItem)
                .where(
                    TransferItem.device_udid == self._udid,
                    TransferItem.status.in_([STATUS_PENDING, STATUS_PARTIAL]),  # type: ignore[union-attr]
                )
                .order_by(TransferItem.id)
            ).first()
            return item.id if item else None

    async def _transfer_item(self, item_id: int) -> None:
        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.status = STATUS_IN_PROGRESS
            session.add(item)
            session.commit()
            remote_path = item.remote_path
            remote_size = item.remote_size_bytes
            file_name = item.file_name
            remote_modified_at = item.remote_modified_at
            db_bytes_transferred = item.bytes_transferred

        local_path = self._destination / self._local_relative_path(
            item_id, file_name, remote_path, remote_modified_at
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = Path(str(local_path) + ".partial")
        offset = self._resume_offset(partial_path, db_bytes_transferred)
        mode = "r+b" if offset > 0 else "wb"

        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.local_path = str(local_path)
            session.add(item)
            session.commit()

        try:
            with open(partial_path, mode) as fh:
                fh.seek(offset)
                chunks_since_flush = 0
                while offset < remote_size:
                    chunk = await self._read_chunk_safe(remote_path, offset, remote_size)
                    fh.write(chunk)
                    offset += len(chunk)
                    chunks_since_flush += 1

                    if chunks_since_flush >= settings.DB_PROGRESS_FLUSH_EVERY_N_CHUNKS:
                        self._flush_progress(item_id, offset)
                        chunks_since_flush = 0
                        await self._emit_progress(item_id, offset, remote_size)

                self._flush_progress(item_id, offset)
                await self._emit_progress(item_id, offset, remote_size)
        except SeekNotSupportedError:
            _logger.warning("transfer_engine: seek unsupported, restarting item_id=%s", item_id)
            partial_path.unlink(missing_ok=True)
            self._flush_progress(item_id, 0, status=STATUS_PENDING)
            await self._transfer_item(item_id)
            return

        partial_path.replace(local_path)
        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.status = STATUS_COPIED
            item.bytes_transferred = remote_size
            session.add(item)
            session.commit()

    def _resume_offset(self, partial_path: Path, db_bytes_transferred: int) -> int:
        if partial_path.exists() and partial_path.stat().st_size == db_bytes_transferred:
            return db_bytes_transferred
        return 0

    async def _read_chunk_safe(self, remote_path: str, offset: int, remote_size: int) -> bytes:
        length = min(settings.TRANSFER_CHUNK_SIZE_BYTES, remote_size - offset)
        try:
            return await asyncio.to_thread(self._afc.read_chunk, remote_path, offset, length)
        except SeekNotSupportedError:
            raise
        except AfcConnectionLostError as exc:
            raise app_error(DEVICE_DISCONNECTED, detail=str(exc)) from exc
        # Anything else (e.g. a single missing/inaccessible file on the device) is
        # this one item's problem, not the whole session's - let it propagate to
        # run()'s outer handler, which marks just this item failed and moves on to
        # the next one. Only AfcConnectionLostError means the connection itself is
        # actually gone (see app/device/afc_client.py) - see docs/DEVELOPMENT.md for
        # the real ~141GB/12k-item transfer where a single AfcFileNotFoundError was
        # misclassified as a full disconnect and paused the entire queue.

    async def _emit_progress(self, item_id: int, bytes_transferred: int, remote_size: int) -> None:
        await self._on_event(
            "transfer_progress",
            TransferProgressEvent(
                item_id=item_id,
                bytes_transferred=bytes_transferred,
                remote_size_bytes=remote_size,
                device_bytes_transferred=self._device_bytes_transferred(),
            ),
        )

    def _device_bytes_transferred(self) -> int:
        """Sum of bytes_transferred across every item for this device, straight from
        the DB - the authoritative total, unlike a frontend-side running total of just
        this session's events (which loses everything on an app restart even though
        the bytes are still genuinely on disk from earlier sessions)."""
        with get_session() as session:
            total = session.exec(
                select(func.sum(TransferItem.bytes_transferred)).where(TransferItem.device_udid == self._udid)
            ).one()
            return total or 0

    def _flush_progress(self, item_id: int, bytes_transferred: int, status: str = STATUS_PARTIAL) -> None:
        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.bytes_transferred = bytes_transferred
            item.status = status
            session.add(item)
            session.commit()

    def _mark_partial(self, item_id: int) -> None:
        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.status = STATUS_PARTIAL
            session.add(item)
            session.commit()

    def _mark_failed(self, item_id: int, message: str) -> None:
        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            item.status = STATUS_FAILED
            item.error_message = message
            local_path = item.local_path
            session.add(item)
            session.commit()
        # A permanently-failed item (e.g. a file the device itself can't open) leaves
        # behind a stale .partial file - usually empty, since most failures happen on
        # the very first chunk - that nothing ever cleaned up. Confirmed live against
        # a real device: 102 such .partial files accumulated in "unknown-date"
        # (matching exactly the items that could never be opened via AFC) with no
        # corresponding real download to show for them.
        if local_path:
            Path(str(local_path) + ".partial").unlink(missing_ok=True)
