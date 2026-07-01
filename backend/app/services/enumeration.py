"""Build/refresh the transfer manifest by walking /DCIM over AFC (spec FR-3, FR-11)."""
from sqlmodel import select

from app.db import get_session
from app.device.afc_client import AfcClient
from app.models import Device, STATUS_PENDING, TransferItem
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


def _walk_files(afc: AfcClient, root: str) -> list:
    """Recursively collect file entries under `root` (e.g. /DCIM/100APPLE/*)."""
    out = []
    stack = [root]
    while stack:
        current = stack.pop()
        out.extend(afc.list_directory(current))
        stack.extend(afc.list_subdirectories(current))
    return out
