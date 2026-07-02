"""Wires IPC methods (docs/ipc_protocol.md) to service-layer functions.

Thin by design (AGENTS.md §9: routers/handlers thin, services do the work). The event
emitter is injected by app.main at startup via `configure()` since it owns stdout.
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlmodel import select

from app.db import get_session
from app.device.afc_client import PymobiledeviceAfcClient
from app.device.pairing import wait_for_trust
from app.ipc.dispatcher import register
from app.models import STATUS_COPIED, STATUS_FAILED, STATUS_PENDING, STATUS_VERIFIED, Device, TransferItem, TransferSession
from app.schemas import (
    DeleteBatchParams,
    DeleteBatchResult,
    DeviceConnectParams,
    DeviceConnectResult,
    DeviceInfo,
    DeviceListResult,
    EmptyParams,
    EmptyResult,
    LibraryEnumerateParams,
    LibraryEnumerateResult,
    SettingsGetResult,
    SettingsSetParams,
    TransferSessionParams,
    TransferStartParams,
    TransferStartResult,
    VerifyStatusParams,
    VerifyStatusResult,
)
from app.services.delete_service import delete_batch as run_delete_batch
from app.services.enumeration import enumerate_library, requeue_missing_local_files
from app.services.transfer_engine import TransferEngine
from app.services.verification import verify_session
from app.state import state
from app.utils.errors import DEVICE_NOT_FOUND, app_error
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]

_emit_event: Optional[EventEmitter] = None


def configure(emit_event: EventEmitter) -> None:
    """Inject the notification emitter used by handlers (app.main owns stdout)."""
    global _emit_event
    _emit_event = emit_event


def _events() -> EventEmitter:
    assert _emit_event is not None, "ipc.handlers.configure() must run before dispatch"
    return _emit_event


@register("device.list", EmptyParams)
async def handle_device_list(_params: EmptyParams) -> DeviceListResult:
    return DeviceListResult(devices=[DeviceInfo(udid=udid) for udid in state.afc_clients])


@register("device.connect", DeviceConnectParams)
async def handle_device_connect(params: DeviceConnectParams) -> DeviceConnectResult:
    paired = await wait_for_trust(params.udid)
    if not paired:
        return DeviceConnectResult(status="timed_out")

    # A prior session for this udid (e.g. before a disconnect) is never torn down by
    # anything else - device_disconnected doesn't close it, since a mid-transfer
    # connection_lost needs the AfcClient to keep existing for resume (see
    # resume_session_if_paused below). Left alone, its background event-loop thread
    # (app/device/afc_client.py) and open AFC/lockdown connection leak forever and can
    # interfere with the new connection attempt. Close it here, right before it's
    # replaced, since at that point we know for certain nothing will use it again.
    old_afc = state.afc_clients.get(params.udid)
    if old_afc is not None:
        try:
            await asyncio.to_thread(old_afc.close)
        except Exception:  # pylint: disable=broad-except
            _logger.warning("ipc.handlers: error closing stale afc client udid=%s", params.udid, exc_info=True)

    # PymobiledeviceAfcClient.__init__ runs its own asyncio.run() internally (see
    # app/device/afc_client.py), so it must be constructed off this event loop.
    afc = await asyncio.to_thread(PymobiledeviceAfcClient, params.udid)
    state.afc_clients[params.udid] = afc

    with get_session() as session:
        device = session.get(Device, params.udid)
        if device is None:
            device = Device(udid=params.udid)
        device.last_connected_at = datetime.now(timezone.utc)
        session.add(device)
        session.commit()

    return DeviceConnectResult(status="connected", device=DeviceInfo(udid=params.udid))


@register("library.enumerate", LibraryEnumerateParams)
async def handle_library_enumerate(params: LibraryEnumerateParams) -> LibraryEnumerateResult:
    afc = state.afc_clients.get(params.udid)
    if afc is None:
        raise app_error(DEVICE_NOT_FOUND)
    total_items, total_bytes = await asyncio.to_thread(enumerate_library, params.udid, afc)
    await asyncio.to_thread(requeue_missing_local_files, params.udid)
    return LibraryEnumerateResult(total_items=total_items, total_bytes=total_bytes)


@register("transfer.start", TransferStartParams)
async def handle_transfer_start(params: TransferStartParams) -> TransferStartResult:
    afc = state.afc_clients.get(params.udid)
    if afc is None:
        raise app_error(DEVICE_NOT_FOUND)

    _retry_failed_items(params.udid)

    session_id = _get_or_create_session(params.udid)

    engine = TransferEngine(params.udid, Path(params.destination), afc, _events())
    state.engines[session_id] = engine
    state.session_udid_by_id[session_id] = params.udid
    asyncio.create_task(_run_transfer_then_verify(session_id, engine, params.udid))

    return TransferStartResult(session_id=session_id)


def _retry_failed_items(udid: str) -> None:
    """Reset failed items back to pending so a fresh Start Transfer retries them.

    Previously, a failed item was stuck forever - _next_item_id only ever selects
    pending/partial, so nothing revisited it. That's a real problem now that a real
    ~12k-item transfer surfaced a filename-collision bug (see transfer_engine.py's
    _local_filename) that left ~1000 items permanently failed with no way to recover
    without restarting the whole device from scratch. Treat "Start Transfer" as
    "finish the job" - retry everything not yet verified, not just what's pending.
    """
    with get_session() as session:
        failed = session.exec(
            select(TransferItem).where(TransferItem.device_udid == udid, TransferItem.status == STATUS_FAILED)
        ).all()
        for item in failed:
            item.status = STATUS_PENDING
            item.error_message = None
            item.bytes_transferred = 0
            session.add(item)
        if failed:
            _logger.info("ipc.handlers: retrying %s previously failed items udid=%s", len(failed), udid)
            session.commit()


def _get_or_create_session(udid: str) -> int:
    with get_session() as session:
        existing = session.exec(
            select(TransferSession).where(TransferSession.device_udid == udid, TransferSession.outcome.is_(None))  # type: ignore[union-attr]
        ).first()
        if existing is not None:
            assert existing.id is not None
            return existing.id

        created = TransferSession(device_udid=udid, started_at=datetime.now(timezone.utc))
        session.add(created)
        session.commit()
        session.refresh(created)
        assert created.id is not None
        return created.id


async def _run_transfer_then_verify(session_id: int, engine: TransferEngine, udid: str) -> None:
    await engine.run()
    await verify_session(udid, _events())

    # verify_session only emits verification_progress for items it actually verified
    # this run (status was copied -> verified). If every item was already verified
    # from a prior run (e.g. relaunching against a device that's already fully
    # transferred - SQLite state persists across restarts), zero progress events fire
    # and the frontend, which only detects "done" by counting them, would be stuck on
    # "Verifying..." forever. This event is the authoritative, always-fired signal.
    with get_session() as session:
        items = session.exec(select(TransferItem).where(TransferItem.device_udid == udid)).all()
        verified_items = [i for i in items if i.status == STATUS_VERIFIED]
    await _events()(
        "verification_complete",
        {
            "verified_count": len(verified_items),
            "total_count": len(items),
            # Same reasoning as verified_count above: the frontend's Free Up Space
            # button needs the *authoritative* list of currently-verified items, not
            # just the ones copied this run (state.transferredByItem), or it silently
            # deletes nothing when re-launched against an already-fully-verified
            # device.
            "item_ids": [i.id for i in verified_items],
        },
    )


async def resume_session_if_paused(udid: str) -> None:
    """Called by app.main when a device reconnects: resume any engine that was
    paused by a `connection_lost` event for this udid (spec §5.4 reconnection)."""
    for session_id, session_udid in state.session_udid_by_id.items():
        if session_udid != udid:
            continue
        engine = state.engines.get(session_id)
        if engine is not None:
            _logger.info("ipc.handlers: resuming session_id=%s after reconnect", session_id)
            asyncio.create_task(_run_transfer_then_verify(session_id, engine, udid))


@register("transfer.pause", TransferSessionParams)
async def handle_transfer_pause(params: TransferSessionParams) -> EmptyResult:
    engine = state.engines.get(params.session_id)
    if engine is not None:
        engine.pause()
    return EmptyResult()


@register("transfer.cancel", TransferSessionParams)
async def handle_transfer_cancel(params: TransferSessionParams) -> EmptyResult:
    engine = state.engines.get(params.session_id)
    if engine is not None:
        engine.cancel()
    return EmptyResult()


@register("verify.status", VerifyStatusParams)
async def handle_verify_status(params: VerifyStatusParams) -> VerifyStatusResult:
    with get_session() as session:
        ts = session.get(TransferSession, params.session_id)
        if ts is None:
            raise app_error(DEVICE_NOT_FOUND)
        items = session.exec(select(TransferItem).where(TransferItem.device_udid == ts.device_udid)).all()
        verified = sum(1 for i in items if i.status == STATUS_VERIFIED)
        pending = sum(1 for i in items if i.status == STATUS_COPIED)
        return VerifyStatusResult(verified_count=verified, pending_count=pending)


@register("delete.batch", DeleteBatchParams)
async def handle_delete_batch(params: DeleteBatchParams) -> DeleteBatchResult:
    if not params.item_ids:
        return DeleteBatchResult(deleted_count=0, failures=[])

    with get_session() as session:
        first_item = session.get(TransferItem, params.item_ids[0])
    if first_item is None:
        raise app_error(DEVICE_NOT_FOUND)

    afc = state.afc_clients.get(first_item.device_udid)
    if afc is None:
        raise app_error(DEVICE_NOT_FOUND)

    deleted_count, failures = await run_delete_batch(params.item_ids, afc, _events())
    return DeleteBatchResult(deleted_count=deleted_count, failures=failures)


@register("settings.get", EmptyParams)
async def handle_settings_get(_params: EmptyParams) -> SettingsGetResult:
    from app.config import settings as app_settings
    from app.services.settings_service import get_all_settings

    return SettingsGetResult(
        values={
            **get_all_settings(),
            "concurrency": app_settings.TRANSFER_CONCURRENCY,
            "log_dir": str(app_settings.APP_DATA_DIR / "logs"),
        }
    )


@register("settings.set", SettingsSetParams)
async def handle_settings_set(params: SettingsSetParams) -> EmptyResult:
    from app.services.settings_service import set_setting

    for key, value in params.values.items():
        if value is not None:
            set_setting(key, str(value))
    return EmptyResult()
