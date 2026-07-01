"""Transfer engine: chunked copy, resume-from-offset, and disconnect handling."""
from pathlib import Path

from app.device.afc_client import MockAfcClient
from app.models import STATUS_COPIED, STATUS_PARTIAL, TransferItem
from app.services.transfer_engine import TransferEngine
from app.db import get_session


def _make_item(remote_path: str, data: bytes) -> int:
    with get_session() as session:
        item = TransferItem(
            device_udid="udid-1",
            remote_path=remote_path,
            file_name=Path(remote_path).name,
            remote_size_bytes=len(data),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


async def test_transfer_copies_full_file(destination, events):
    data = b"x" * 1000
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0001.HEIC": data})
    _make_item("/DCIM/100APPLE/IMG_0001.HEIC", data)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    local_file = destination / "IMG_0001.HEIC"
    assert local_file.exists()
    assert local_file.read_bytes() == data


async def test_transfer_marks_item_copied(destination, events):
    data = b"y" * 500
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0002.HEIC": data})
    item_id = _make_item("/DCIM/100APPLE/IMG_0002.HEIC", data)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_COPIED
        assert item.bytes_transferred == len(data)


async def test_resume_seeks_to_last_confirmed_offset(destination, events):
    """A .partial file whose size matches DB bytes_transferred must resume, not
    restart — spec §5.4 resume semantics, FR-5."""
    full_data = b"a" * 4096 + b"b" * 4096
    remote_path = "/DCIM/100APPLE/IMG_0003.HEIC"
    afc = MockAfcClient({remote_path: full_data})
    item_id = _make_item(remote_path, full_data)

    partial_path = destination / "IMG_0003.HEIC.partial"
    partial_path.write_bytes(full_data[:4096])
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        item.bytes_transferred = 4096
        item.status = STATUS_PARTIAL
        item.local_path = str(destination / "IMG_0003.HEIC")
        session.add(item)
        session.commit()

    # Sabotage the remote copy for the already-transferred range so a correct resume
    # (which never re-reads those bytes) still produces the right file, while a bug
    # that restarts from zero would copy the sabotaged bytes and fail the assertion.
    afc._files[remote_path] = b"Z" * 4096 + full_data[4096:]  # noqa: SLF001

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    final_path = destination / "IMG_0003.HEIC"
    assert final_path.read_bytes() == full_data


async def test_disconnect_marks_item_partial_and_emits_connection_lost(destination, events):
    remote_path = "/DCIM/100APPLE/IMG_0004.HEIC"
    data = b"z" * 8192
    item_id = _make_item(remote_path, data)

    class DisconnectingAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise OSError("device disconnected")

    afc = DisconnectingAfc({remote_path: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_PARTIAL

    assert any(event == "connection_lost" for event, _ in events.collected)
