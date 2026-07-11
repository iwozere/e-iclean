"""Verification: size + checksum, and the copied -> verified promotion (spec §5.6)."""
import hashlib

from app.db import get_session
from app.models import STATUS_COPIED, STATUS_FAILED, STATUS_VERIFIED, TransferItem
from app.services.verification import verify_one, verify_session


def _make_copied_item(destination, file_name: str, data: bytes) -> int:
    local_path = destination / file_name
    local_path.write_bytes(data)
    with get_session() as session:
        item = TransferItem(
            device_udid="udid-1",
            remote_path=f"/DCIM/100APPLE/{file_name}",
            file_name=file_name,
            remote_size_bytes=len(data),
            local_path=str(local_path),
            bytes_transferred=len(data),
            status=STATUS_COPIED,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


def test_verify_one_promotes_matching_file(destination):
    data = b"hello world"
    item_id = _make_copied_item(destination, "IMG_0001.HEIC", data)

    assert verify_one(item_id) is True

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item is not None
        assert item.status == STATUS_VERIFIED
        assert item.checksum_local == hashlib.sha256(data).hexdigest()


def test_verify_one_fails_on_size_mismatch(destination):
    item_id = _make_copied_item(destination, "IMG_0002.HEIC", b"1234567890")
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item is not None
        item.remote_size_bytes = 999999
        session.add(item)
        session.commit()

    assert verify_one(item_id) is False

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item is not None
        assert item.status == STATUS_FAILED


async def test_verify_session_emits_progress_per_item(destination, events):
    _make_copied_item(destination, "IMG_0003.HEIC", b"a")
    _make_copied_item(destination, "IMG_0004.HEIC", b"b")

    verified_count, failed_count = await verify_session("udid-1", events)

    assert verified_count == 2
    assert failed_count == 0
    assert len([e for e in events.collected if e[0] == "verification_progress"]) == 2
