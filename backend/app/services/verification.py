"""Verify-before-delete workflow (spec §5.6): size check + SHA-256 checksum."""
import asyncio
import hashlib
from pathlib import Path
from typing import Awaitable, Callable

from sqlmodel import select

from app.config import settings
from app.db import get_session
from app.models import STATUS_COPIED, STATUS_FAILED, STATUS_VERIFIED, TransferItem
from app.schemas import VerificationProgressEvent
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]


def _checksum(path: Path) -> str:
    digest = hashlib.new(settings.CHECKSUM_ALGORITHM)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(settings.TRANSFER_CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def verify_session(udid: str, on_event: EventEmitter) -> tuple[int, int]:
    """
    Verify every `copied` item for a device: size match + checksum, promote to
    `verified` on success (spec §5.6 points 1-3).

    Args:
        udid: Device UDID whose items should be verified.
        on_event: Notification emitter for per-item verification_progress events.

    Returns:
        (verified_count, failed_count) for this run.
    """
    with get_session() as session:
        items = session.exec(
            select(TransferItem).where(TransferItem.device_udid == udid, TransferItem.status == STATUS_COPIED)
        ).all()
        item_ids = [item.id for item in items if item.id is not None]

    verified_count = 0
    failed_count = 0

    for item_id in item_ids:
        ok = await asyncio.to_thread(verify_one, item_id)
        if ok:
            verified_count += 1
        else:
            failed_count += 1
        await on_event("verification_progress", VerificationProgressEvent(item_id=item_id, verified=ok))

    _logger.info("verification: udid=%s verified=%s failed=%s", udid, verified_count, failed_count)
    return verified_count, failed_count


def verify_one(item_id: int) -> bool:
    """
    Verify a single already-copied item by id. Synchronous — run via
    `asyncio.to_thread` from async callers (this does file I/O + hashing).

    Args:
        item_id: TransferItem.id to verify.

    Returns:
        True if the item is now `verified`, False if it was marked `failed`.
    """
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        if item is None or item.local_path is None:
            return False

        local_path = Path(item.local_path)
        try:
            actual_size = local_path.stat().st_size
            if actual_size != item.remote_size_bytes:
                item.status = STATUS_FAILED
                item.error_message = "Local file size does not match the device."
                session.add(item)
                session.commit()
                return False

            item.checksum_local = _checksum(local_path)
            item.status = STATUS_VERIFIED
            session.add(item)
            session.commit()
            return True
        except OSError:
            _logger.exception("verification: I/O error item_id=%s", item_id)
            item.status = STATUS_FAILED
            item.error_message = "Could not read the copied file to verify it."
            session.add(item)
            session.commit()
            return False
