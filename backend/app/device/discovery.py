"""usbmux device watcher: polls connected devices and emits connect/disconnect events.

Polling (rather than an OS-level USB event subscription) matches spec §5.4: usbmux
doesn't expose a reliable cross-platform push API via pymobiledevice3, so a 1-2 second
poll loop is the documented approach (FR-1).
"""
import asyncio
from typing import Awaitable, Callable, Optional

from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError

from app.config import settings
from app.schemas import DeviceConnectedEvent, DeviceDisconnectedEvent
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]
ListConnectedUdids = Callable[[], Awaitable[set]]


async def default_list_connected_udids() -> set:
    """Real implementation: confined pymobiledevice3 import (AGENTS.md §9).

    `list_devices` is a coroutine function as of pymobiledevice3 9.32.0 (confirmed
    empirically - it was not always async in older versions, see spec §9.1).
    """
    from pymobiledevice3.usbmux import list_devices

    devices = await list_devices()
    return {device.serial for device in devices}


class DeviceWatcher:
    """Polls for connected device UDIDs and emits device_connected/disconnected."""

    def __init__(
        self,
        on_event: EventEmitter,
        list_connected_udids: ListConnectedUdids = default_list_connected_udids,
        poll_interval: Optional[float] = None,
    ) -> None:
        self._on_event = on_event
        self._list_connected_udids = list_connected_udids
        self._poll_interval = poll_interval if poll_interval is not None else settings.DEVICE_POLL_INTERVAL_SECONDS
        self._known: set = set()
        self._driver_missing = False

    async def run(self) -> None:
        """Poll forever until the enclosing task is cancelled."""
        while True:
            await self.poll_once()
            await asyncio.sleep(self._poll_interval)

    async def poll_once(self) -> None:
        """Run a single poll iteration; public so tests can drive it deterministically."""
        try:
            current = await self._list_connected_udids()
        except ConnectionFailedToUsbmuxdError:
            # No usbmux daemon reachable at all - almost always means Apple Mobile
            # Device Support isn't installed on this machine (see backend/BUILD.md
            # "Known issues"). Distinguished from other failures so the UI can show
            # actionable help instead of silently sitting on "Connect your iPhone".
            _logger.warning("device_watcher: usbmuxd unreachable (Apple Mobile Device Support missing?)")
            if not self._driver_missing:
                self._driver_missing = True
                await self._on_event("driver_missing", {})
            return
        except Exception:  # pylint: disable=broad-except
            _logger.exception("device_watcher: failed to list connected devices")
            return

        if self._driver_missing:
            self._driver_missing = False
            await self._on_event("driver_available", {})

        for udid in current - self._known:
            _logger.info("device_watcher: connected udid=%s", udid)
            await self._on_event("device_connected", DeviceConnectedEvent(udid=udid))

        for udid in self._known - current:
            _logger.warning("device_watcher: disconnected udid=%s", udid)
            await self._on_event("device_disconnected", DeviceDisconnectedEvent(udid=udid))

        self._known = current
