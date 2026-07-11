"""Reconnect/resume flow (backend/app/ipc/handlers.py): a mid-transfer disconnect must
not prematurely report the rest of the queue as failed, and resuming after reconnect
must use the freshly-established AfcClient rather than the old engine's stale one.

See docs/DEVELOPMENT.md for the real-world bug this covers: a disconnect during a
~12k-item transfer jumped straight to the "Free Up Space" screen reporting thousands of
never-attempted items as unrecoverable, and Start Transfer after Re-check Library looked
like a no-op because a stray background resume (against a closed AfcClient) kept
re-emitting verification_complete and snapping the UI back."""
import asyncio

from app.db import get_session
from app.device.afc_client import AfcConnectionLostError, MockAfcClient
from app.ipc import handlers
from app.models import STATUS_PENDING, STATUS_VERIFIED, TransferItem
from app.services.transfer_engine import TransferEngine
from app.state import state


def _make_item(udid: str, remote_path: str, data: bytes) -> int:
    with get_session() as session:
        item = TransferItem(
            device_udid=udid,
            remote_path=remote_path,
            file_name=remote_path.rsplit("/", 1)[-1],
            remote_size_bytes=len(data),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


def _reset_state():
    state.afc_clients.clear()
    state.engines.clear()
    state.session_udid_by_id.clear()


async def test_disconnect_does_not_emit_verification_complete(destination, events):
    """A disconnect only 1 item into a larger queue must leave the rest `pending` and
    must not run verification/emit verification_complete - see
    TransferEngine.run()'s OUTCOME_* return and _run_transfer_then_verify's check."""
    _reset_state()
    handlers.configure(events)
    udid = "udid-1"
    remote_path_a = "/DCIM/100APPLE/IMG_0001.HEIC"
    remote_path_b = "/DCIM/100APPLE/IMG_0002.HEIC"
    data = b"z" * 8192
    _make_item(udid, remote_path_a, data)
    item_b_id = _make_item(udid, remote_path_b, data)

    class DisconnectingAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise AfcConnectionLostError("device disconnected")

    afc = DisconnectingAfc({remote_path_a: data, remote_path_b: data})
    engine = TransferEngine(udid, destination, afc, events)

    try:
        await handlers._run_transfer_then_verify(1, engine, udid)  # noqa: SLF001

        assert not any(event == "verification_complete" for event, _ in events.collected)
        assert any(event == "connection_lost" for event, _ in events.collected)
        with get_session() as session:
            item_b = session.get(TransferItem, item_b_id)
            assert item_b is not None
            assert item_b.status == STATUS_PENDING
    finally:
        _reset_state()


async def test_resume_after_reconnect_uses_fresh_afc_client(destination, events):
    """resume_session_if_paused must swap the engine onto the just-reconnected client -
    reusing the old (now-closed) one would immediately re-disconnect."""
    _reset_state()
    handlers.configure(events)
    udid = "udid-1"
    remote_path = "/DCIM/100APPLE/IMG_0001.HEIC"
    data = b"z" * 8192
    item_id = _make_item(udid, remote_path, data)

    class DeadAfc(MockAfcClient):
        def read_chunk(self, remote_path: str, offset: int, length: int) -> bytes:
            raise AfcConnectionLostError("stale connection, already closed")

    dead_afc = DeadAfc({remote_path: data})
    fresh_afc = MockAfcClient({remote_path: data})

    session_id = 1
    engine = TransferEngine(udid, destination, dead_afc, events)
    state.engines[session_id] = engine
    state.session_udid_by_id[session_id] = udid

    try:
        # resume_session_if_paused kicks off the resumed run via asyncio.create_task
        # (fire-and-forget, so handle_device_connect doesn't block on the whole
        # resumed transfer) - await the task it spawned to observe the outcome.
        tasks_before = asyncio.all_tasks()
        await handlers.resume_session_if_paused(udid, fresh_afc)
        spawned = asyncio.all_tasks() - tasks_before
        await asyncio.gather(*spawned)

        with get_session() as session:
            item = session.get(TransferItem, item_id)
            assert item is not None
            assert item.status == STATUS_VERIFIED
        assert any(event == "verification_complete" for event, _ in events.collected)
    finally:
        _reset_state()
