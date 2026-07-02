"""Build/refresh the transfer manifest by walking /DCIM over AFC (spec FR-3, FR-11)."""
from pathlib import Path

from sqlmodel import select

from app.db import get_session
from app.device.afc_client import AfcClient
from app.models import STATUS_COPIED, STATUS_PENDING, STATUS_VERIFIED, Device, TransferItem
from app.services.live_photo import pair_live_photos
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

DCIM_ROOT = "/DCIM"


def enumerate_library(udid: str, afc: AfcClient) -> tuple[int, int]:
    """
    Walk /DCIM via AFC, upsert the transfer manifest in SQLite (idempotent — existing
    rows are left untouched, satisfying FR-11), and link any new Live Photo pairs.

    Args:
        udid: Device UDID this library belongs to.
        afc: Connected AFC client for that device.

    Returns:
        (total_items, total_bytes) currently known for this device.
    """
    with get_session() as session:
        if session.get(Device, udid) is None:
            session.add(Device(udid=udid))
            session.commit()

        for entry in _walk_files(afc, DCIM_ROOT):
            existing = session.exec(
                select(TransferItem).where(
                    TransferItem.device_udid == udid,
                    TransferItem.remote_path == entry.path,
                )
            ).first()
            if existing is not None:
                # Backfill a date that was missing when this row was first created -
                # e.g. every row enumerated before PymobiledeviceAfcClient actually
                # started reading AFC's st_mtime (see afc_client.py) has
                # remote_modified_at=NULL forever otherwise, since this loop is
                # upsert-only for *new* remote_path values and never revisits
                # existing rows. Confirmed as a real gap: a user's entire library
                # (14k+ items across two devices) landed in the "unknown-date" bucket
                # because of exactly this - the date-population fix only affected
                # items discovered from that point on, not ones already in the DB.
                if existing.remote_modified_at is None and entry.modified_at is not None:
                    existing.remote_modified_at = entry.modified_at
                    session.add(existing)
                continue
            session.add(
                TransferItem(
                    device_udid=udid,
                    remote_path=entry.path,
                    file_name=entry.path.rsplit("/", 1)[-1],
                    remote_size_bytes=entry.size,
                    remote_modified_at=entry.modified_at,
                    status=STATUS_PENDING,
                )
            )
        session.commit()

        pair_live_photos(session, udid)

        items = session.exec(select(TransferItem).where(TransferItem.device_udid == udid)).all()
        total_bytes = sum(item.remote_size_bytes for item in items)
        _logger.info("enumeration: udid=%s items=%s bytes=%s", udid, len(items), total_bytes)
        return len(items), total_bytes


def requeue_missing_local_files(udid: str) -> int:
    """Reset items whose local copy is gone (or no longer matches) back to pending.

    `copied`/`verified` status in SQLite was previously treated as permanent, but the
    actual file on disk can vanish independently at any time (user deletes/moves the
    destination folder, disk cleanup, etc.) - nothing ever re-checked it. Confirmed as
    a real gap: deleting the whole destination folder and reconnecting the same,
    already-fully-verified device jumped straight to the Free Up Space screen instead
    of re-downloading anything, since every item still said `verified` in the DB.
    Runs on every `library.enumerate` (device connect, and the UI's "Re-check
    Library" action) so `transfer.start` always sees an accurate pending queue.

    Returns:
        Number of items reset to pending.
    """
    with get_session() as session:
        items = session.exec(
            select(TransferItem).where(
                TransferItem.device_udid == udid,
                TransferItem.status.in_([STATUS_COPIED, STATUS_VERIFIED]),  # type: ignore[union-attr]
            )
        ).all()
        reset = 0
        for item in items:
            if not _local_file_matches(item.local_path, item.remote_size_bytes):
                item.status = STATUS_PENDING
                item.bytes_transferred = 0
                item.checksum_local = None
                item.error_message = None
                session.add(item)
                reset += 1
        if reset:
            _logger.info("enumeration: udid=%s requeued %s items with missing/changed local files", udid, reset)
            session.commit()
        return reset


def _local_file_matches(local_path, expected_size: int) -> bool:
    if not local_path:
        return False
    try:
        return Path(local_path).stat().st_size == expected_size
    except OSError:
        return False


def _walk_files(afc: AfcClient, root: str) -> list:
    """Recursively collect file entries under `root` (e.g. /DCIM/100APPLE/*)."""
    out = []
    stack = [root]
    while stack:
        current = stack.pop()
        out.extend(afc.list_directory(current))
        stack.extend(afc.list_subdirectories(current))
    return out
