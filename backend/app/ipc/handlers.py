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
from app.models import STATUS_COPIED, STATUS_VERIFIED, Device, TransferItem, TransferSession
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
from app.services.enumeration import enumerate_library
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
    return LibraryEnumerateResult(total_items=total_items, total_bytes=total_bytes)


@register("transfer.start", TransferStartParams)
async def handle_transfer_start(params: TransferStartParams) -> TransferStartResult:
    afc = state.afc_clients.get(params.udid)
    if afc is None:
        raise app_error(DEVICE_NOT_FOUND)

    session_id = _get_or_create_session(params.udid)

    engine = TransferEngine(params.udid, Path(params.destination), afc, _events())
    state.engines[session_id] = engine
    state.session_udid_by_id[session_id] = params.udid
    asyncio.create_task(_run_transfer_then_verify(session_id, engine, params.udid))

    return TransferStartResult(session_id=session_id)


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

    return SettingsGetResult(
        values={
            "concurrency": app_settings.TRANSFER_CONCURRENCY,
            "log_dir": str(app_settings.APP_DATA_DIR / "logs"),
        }
    )


@register("settings.set", SettingsSetParams)
async def handle_settings_set(_params: SettingsSetParams) -> EmptyResult:
    # MVP gap: settings persistence beyond process defaults is not yet implemented.
    # See README "Known gaps" — destination-folder default and concurrency toggle
    # need a small key/value settings table before this can do more than no-op.
    return EmptyResult()
