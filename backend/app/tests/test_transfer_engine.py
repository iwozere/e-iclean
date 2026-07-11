"""Transfer engine: chunked copy, resume-from-offset, date-based destination nesting,
and disconnect handling."""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.device.afc_client import AfcConnectionLostError, MockAfcClient
from app.models import STATUS_COPIED, STATUS_FAILED, STATUS_PARTIAL, STATUS_PENDING, TransferItem
from app.services.transfer_engine import OUTCOME_DISCONNECTED, OUTCOME_DRAINED, TransferEngine
from app.db import get_session

JULY = datetime(2026, 7, 15, 12, 0, 0)
AUGUST = datetime(2026, 8, 3, 9, 0, 0)


def _make_item(remote_path: str, data: bytes, modified_at: Optional[datetime] = JULY) -> int:
    with get_session() as session:
        item = TransferItem(
            device_udid="udid-1",
            remote_path=remote_path,
            file_name=Path(remote_path).name,
            remote_size_bytes=len(data),
            remote_modified_at=modified_at,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


async def test_transfer_copies_full_file_into_date_bucket(destination, events):
    data = b"x" * 1000
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0001.HEIC": data})
    _make_item("/DCIM/100APPLE/IMG_0001.HEIC", data, JULY)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    local_file = destination / "2026-07" / "IMG_0001.HEIC"
    assert local_file.exists()
    assert local_file.read_bytes() == data


async def test_transfer_marks_item_copied(destination, events):
    data = b"y" * 500
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0002.HEIC": data})
    item_id = _make_item("/DCIM/100APPLE/IMG_0002.HEIC", data, JULY)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_COPIED
        assert item.bytes_transferred == len(data)


async def test_unknown_modified_date_falls_back_to_unknown_date_bucket(destination, events):
    data = b"w" * 100
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0009.HEIC": data})
    _make_item("/DCIM/100APPLE/IMG_0009.HEIC", data, modified_at=None)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    local_file = destination / "unknown-date" / "IMG_0009.HEIC"
    assert local_file.read_bytes() == data


async def test_resume_seeks_to_last_confirmed_offset(destination, events):
    """A .partial file whose size matches DB bytes_transferred must resume, not
    restart — spec §5.4 resume semantics, FR-5."""
    full_data = b"a" * 4096 + b"b" * 4096
    remote_path = "/DCIM/100APPLE/IMG_0003.HEIC"
    afc = MockAfcClient({remote_path: full_data})
    item_id = _make_item(remote_path, full_data, JULY)

    month_dir = destination / "2026-07"
    month_dir.mkdir(parents=True, exist_ok=True)
    partial_path = month_dir / "IMG_0003.HEIC.partial"
    partial_path.write_bytes(full_data[:4096])
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        item.bytes_transferred = 4096
        item.status = STATUS_PARTIAL
        item.local_path = str(month_dir / "IMG_0003.HEIC")
        session.add(item)
        session.commit()

    # Sabotage the remote copy for the already-transferred range so a correct resume
    # (which never re-reads those bytes) still produces the right file, while a bug
    # that restarts from zero would copy the sabotaged bytes and fail the assertion.
    afc._files[remote_path] = b"Z" * 4096 + full_data[4096:]  # noqa: SLF001

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    final_path = month_dir / "IMG_0003.HEIC"
    assert final_path.read_bytes() == full_data


async def test_same_filename_different_month_needs_no_disambiguation(destination, events):
    """Date-based nesting alone should separate these - no '(NNNAPPLE)' suffix
    needed, since they land in different month folders."""
    data_a = b"a" * 1000
    data_b = b"b" * 2000
    remote_a = "/DCIM/100APPLE/IMG_0005.HEIC"
    remote_b = "/DCIM/205APPLE/IMG_0005.HEIC"
    afc = MockAfcClient({remote_a: data_a, remote_b: data_b})
    _make_item(remote_a, data_a, JULY)
    _make_item(remote_b, data_b, AUGUST)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    assert (destination / "2026-07" / "IMG_0005.HEIC").read_bytes() == data_a
    assert (destination / "2026-08" / "IMG_0005.HEIC").read_bytes() == data_b


async def test_same_filename_same_month_still_disambiguated(destination, events):
    """iPhone photo libraries can have two different files with the same bare name in
    different DCIM/NNNAPPLE folders *and* the same capture month (the NNN and IMG_
    counters both wrap over a large enough library) - confirmed against a real
    ~12k-item library where this silently overwrote one file with another's content.
    Date nesting alone doesn't catch this case, so the folder-name suffix fallback
    must still kick in."""
    data_a = b"a" * 1000
    data_b = b"b" * 2000
    remote_a = "/DCIM/100APPLE/IMG_0006.HEIC"
    remote_b = "/DCIM/205APPLE/IMG_0006.HEIC"
    afc = MockAfcClient({remote_a: data_a, remote_b: data_b})
    _make_item(remote_a, data_a, JULY)
    _make_item(remote_b, data_b, JULY)

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    local_a = destination / "2026-07" / "IMG_0006 (100APPLE).HEIC"
    local_b = destination / "2026-07" / "IMG_0006 (205APPLE).HEIC"
    assert local_a.read_bytes() == data_a
    assert local_b.read_bytes() == data_b


async def test_disconnect_marks_item_partial_and_emits_connection_lost(destination, events):
    remote_path = "/DCIM/100APPLE/IMG_0004.HEIC"
    data = b"z" * 8192
    item_id = _make_item(remote_path, data, JULY)

    class DisconnectingAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise AfcConnectionLostError("device disconnected")

    afc = DisconnectingAfc({remote_path: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    outcome = await engine.run()

    assert outcome == OUTCOME_DISCONNECTED

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item.status == STATUS_PARTIAL

    assert any(event == "connection_lost" for event, _ in events.collected)


async def test_disconnect_leaves_other_pending_items_untouched(destination, events):
    """A disconnect on item 1 of many must not silently touch the rest of the queue -
    they stay `pending` for a later resume/retry, not `failed` (see
    docs/DEVELOPMENT.md: the caller distinguishing OUTCOME_DISCONNECTED from
    OUTCOME_DRAINED is what a mid-transfer connection loss depends on)."""
    remote_path_a = "/DCIM/100APPLE/IMG_0001.HEIC"
    remote_path_b = "/DCIM/100APPLE/IMG_0002.HEIC"
    data = b"z" * 8192
    _make_item(remote_path_a, data, JULY)
    item_b_id = _make_item(remote_path_b, data, JULY)

    class DisconnectingAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise AfcConnectionLostError("device disconnected")

    afc = DisconnectingAfc({remote_path_a: data, remote_path_b: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    with get_session() as session:
        item_b = session.get(TransferItem, item_b_id)
        assert item_b.status == STATUS_PENDING


async def test_per_file_error_fails_only_that_item_and_continues_queue(destination, events):
    """A single missing/inaccessible file (pymobiledevice3's AfcFileNotFoundError in
    the real client) must NOT be treated as a disconnect - confirmed live against a
    real ~141GB/12k-item transfer, where one bad file (out of 12174) was
    misclassified as AfcConnectionLostError and paused the *entire* queue, showing a
    misleading "Connection lost" banner. Only that one item should fail; the rest of
    the queue must still drain."""
    remote_path_bad = "/DCIM/141APPLE/IMG_1843.HEIC"
    remote_path_ok = "/DCIM/100APPLE/IMG_0002.HEIC"
    data = b"z" * 8192
    bad_item_id = _make_item(remote_path_bad, data, JULY)
    ok_item_id = _make_item(remote_path_ok, data, JULY)

    class OneBadFileAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            if remote_path == remote_path_bad:
                raise FileNotFoundError(f"[Errno 8] Opcode: FILE_OPEN failed with status: 8: {remote_path}")
            return super().read_chunk(remote_path, offset, length)

    afc = OneBadFileAfc({remote_path_bad: data, remote_path_ok: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    outcome = await engine.run()

    assert outcome == OUTCOME_DRAINED
    assert not any(event == "connection_lost" for event, _ in events.collected)

    with get_session() as session:
        bad_item = session.get(TransferItem, bad_item_id)
        ok_item = session.get(TransferItem, ok_item_id)
        assert bad_item is not None and bad_item.status == STATUS_FAILED
        assert ok_item is not None and ok_item.status == STATUS_COPIED


async def test_permanently_failed_item_does_not_leave_stale_partial_file(destination, events):
    """A permanently-failed item (e.g. a file the device can't open) must not leave a
    stale .partial file behind - confirmed live against a real device: 102 such
    .partial files (most 0 bytes) accumulated in "unknown-date" over a long session,
    one per permanently-failed item, with no real download behind any of them."""
    remote_path = "/DCIM/100APPLE/IMG_0001.HEIC"
    data = b"z" * 8192
    _make_item(remote_path, data, JULY)

    class AlwaysFailsAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise FileNotFoundError(f"[Errno 8] Opcode: FILE_OPEN failed with status: 8: {remote_path}")

    afc = AlwaysFailsAfc({remote_path: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    await engine.run()

    partial_path = destination / "2026-07" / "IMG_0001.HEIC.partial"
    assert not partial_path.exists()


async def test_concurrent_run_calls_serialize_instead_of_racing(destination, events):
    """Two run() calls on the same engine must never execute concurrently - confirmed
    live against a real device: a reconnect racing a still-in-flight run() (the old,
    dying client's read retries hadn't finished failing yet) spawned a second run()
    via resume_session_if_paused, briefly racing the original loop on the same item
    queue. The lock must make a second call queue up rather than run alongside the
    first."""
    remote_path = "/DCIM/100APPLE/IMG_0001.HEIC"
    data = b"z" * 8192
    _make_item(remote_path, data, JULY)
    afc = MockAfcClient({remote_path: data})
    engine = TransferEngine("udid-1", destination, afc, events)

    await engine._run_lock.acquire()  # noqa: SLF001 - simulate a run() already in flight
    try:
        task = asyncio.ensure_future(engine.run())
        await asyncio.sleep(0)  # let the second call start and block on the lock
        assert not task.done()
    finally:
        engine._run_lock.release()  # noqa: SLF001

    outcome = await task
    assert outcome == OUTCOME_DRAINED


async def test_drained_queue_returns_drained_outcome(destination, events):
    remote_path = "/DCIM/100APPLE/IMG_0004.HEIC"
    data = b"z" * 8192
    _make_item(remote_path, data, JULY)
    afc = MockAfcClient({remote_path: data})

    engine = TransferEngine("udid-1", destination, afc, events)
    outcome = await engine.run()

    assert outcome == OUTCOME_DRAINED
