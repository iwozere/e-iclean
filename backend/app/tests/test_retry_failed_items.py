"""transfer.start must retry previously-failed/orphaned items, not leave them stuck
forever (see backend/app/ipc/handlers.py::_retry_failed_items) - surfaced by a real
~12k-item transfer where a filename-collision bug (transfer_engine.py) permanently
failed ~1000 items with no way to recover short of re-enumerating the whole device."""
from app.db import get_session
from app.ipc.handlers import _retry_failed_items  # noqa: SLF001
from app.models import STATUS_FAILED, STATUS_IN_PROGRESS, STATUS_PENDING, STATUS_VERIFIED, TransferItem


def _make_item(status: str) -> int:
    with get_session() as session:
        item = TransferItem(
            device_udid="udid-1",
            remote_path=f"/DCIM/100APPLE/IMG_{status}.HEIC",
            file_name=f"IMG_{status}.HEIC",
            remote_size_bytes=1000,
            status=status,
            error_message="Local file size does not match the device." if status == STATUS_FAILED else None,
            bytes_transferred=1000 if status == STATUS_FAILED else 0,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


def test_retry_resets_failed_items_to_pending():
    failed_id = _make_item(STATUS_FAILED)

    _retry_failed_items("udid-1")

    with get_session() as session:
        item = session.get(TransferItem, failed_id)
        assert item is not None
        assert item.status == STATUS_PENDING
        assert item.error_message is None
        assert item.bytes_transferred == 0


def test_retry_leaves_other_statuses_untouched():
    verified_id = _make_item(STATUS_VERIFIED)

    _retry_failed_items("udid-1")

    with get_session() as session:
        item = session.get(TransferItem, verified_id)
        assert item is not None
        assert item.status == STATUS_VERIFIED


def test_retry_resets_orphaned_in_progress_items_to_pending():
    """An item left `in_progress` when the app closes/crashes mid-copy is otherwise
    stuck forever - confirmed live against a real device (items still in_progress
    from a transfer interrupted hours earlier) - _next_item_id only ever selects
    pending/partial, and no TransferEngine instance survives a restart to finish it."""
    in_progress_id = _make_item(STATUS_IN_PROGRESS)

    _retry_failed_items("udid-1")

    with get_session() as session:
        item = session.get(TransferItem, in_progress_id)
        assert item is not None
        assert item.status == STATUS_PENDING
