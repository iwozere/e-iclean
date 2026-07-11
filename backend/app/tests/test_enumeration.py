"""Library enumeration: recursive /DCIM walk + idempotent re-runs (FR-3, FR-11)."""
from datetime import datetime

from sqlmodel import select

from app.db import get_session
from app.device.afc_client import MockAfcClient
from app.models import TransferItem
from app.services.enumeration import enumerate_library


def test_enumerate_backfills_missing_modified_date_on_existing_item():
    """A row created before the AFC client populated modified dates (remote_
    modified_at=NULL forever otherwise, since this loop only inserts *new*
    remote_path values) must get backfilled on a later re-enumerate - confirmed as a
    real gap where a user's entire library landed in the date-nesting "unknown-date"
    bucket because of exactly this."""
    remote_path = "/DCIM/100APPLE/IMG_0001.HEIC"
    afc = MockAfcClient({remote_path: b"a" * 100})
    enumerate_library("udid-1", afc)  # first run: no modified_at known yet

    with get_session() as session:
        item = session.exec(select(TransferItem).where(TransferItem.remote_path == remote_path)).first()
        assert item is not None
        assert item.remote_modified_at is None

    afc.modified_at[remote_path] = datetime(2026, 7, 15, 12, 0, 0)
    enumerate_library("udid-1", afc)  # second run: device now reports a date

    with get_session() as session:
        item = session.exec(select(TransferItem).where(TransferItem.remote_path == remote_path)).first()
        assert item is not None
        assert item.remote_modified_at == datetime(2026, 7, 15, 12, 0, 0)


def test_enumerate_walks_nested_dcim_subfolders():
    afc = MockAfcClient(
        {
            "/DCIM/100APPLE/IMG_0001.HEIC": b"a" * 100,
            "/DCIM/100APPLE/IMG_0002.MOV": b"b" * 200,
            "/DCIM/101APPLE/IMG_0003.HEIC": b"c" * 50,
        }
    )

    total_items, total_bytes = enumerate_library("udid-1", afc)

    assert total_items == 3
    assert total_bytes == 350


def test_enumerate_is_idempotent_on_rerun():
    """Re-running against the same library must not duplicate rows (FR-11)."""
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0001.HEIC": b"a" * 100})

    enumerate_library("udid-1", afc)
    total_items, total_bytes = enumerate_library("udid-1", afc)

    assert total_items == 1
    assert total_bytes == 100
