"""library.enumerate must catch local files that vanished after being verified
(app.services.enumeration.requeue_missing_local_files) - surfaced by a real user
deleting the whole destination folder and reconnecting an already-fully-verified
device, which jumped straight to Free Up Space instead of re-downloading anything."""
from app.db import get_session
from app.models import STATUS_COPIED, STATUS_PENDING, STATUS_VERIFIED, TransferItem
from app.services.enumeration import requeue_missing_local_files


def _make_item(status: str, local_path, size: int = 1000) -> int:
    with get_session() as session:
        item = TransferItem(
            device_udid="udid-1",
            remote_path=f"/DCIM/100APPLE/{local_path or 'none'}.HEIC",
            file_name=f"{local_path or 'none'}.HEIC",
            remote_size_bytes=size,
            status=status,
            local_path=str(local_path) if local_path else None,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


def test_requeues_verified_item_whose_file_is_gone(tmp_path):
    missing_path = tmp_path / "gone.HEIC"  # never created
    item_id = _make_item(STATUS_VERIFIED, missing_path)

    reset_count = requeue_missing_local_files("udid-1")

    assert reset_count == 1
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_PENDING
        assert item.bytes_transferred == 0


def test_requeues_item_whose_file_size_changed(tmp_path):
    path = tmp_path / "changed.HEIC"
    path.write_bytes(b"x" * 5)  # different from the 1000 bytes recorded in the DB
    item_id = _make_item(STATUS_COPIED, path)

    requeue_missing_local_files("udid-1")

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_PENDING


def test_leaves_intact_verified_item_untouched(tmp_path):
    path = tmp_path / "intact.HEIC"
    path.write_bytes(b"x" * 1000)
    item_id = _make_item(STATUS_VERIFIED, path)

    reset_count = requeue_missing_local_files("udid-1")

    assert reset_count == 0
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_VERIFIED
