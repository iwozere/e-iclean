"""Trust / pairing flow: poll until the user accepts "Trust This Computer" or times out."""
import asyncio
import time
from typing import Awaitable, Callable, Optional

from app.config import settings
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

IsPaired = Callable[[str], Awaitable[bool]]
RequestPairing = Callable[[str], Awaitable[bool]]


async def default_is_paired(udid: str) -> bool:
    """`create_using_usbmux` is a coroutine function as of pymobiledevice3 9.32.0
    (confirmed empirically, see spec §9.1 - API surface drifts across versions)."""
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=udid, autopair=False)
    try:
        return bool(lockdown.paired)
    finally:
        await lockdown.close()


async def default_request_pairing(udid: str) -> bool:
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=udid, autopair=True)
    try:
        return bool(lockdown.paired)
    finally:
        await lockdown.close()


async def wait_for_trust(
    udid: str,
    is_paired: IsPaired = default_is_paired,
    request_pairing: RequestPairing = default_request_pairing,
    timeout: Optional[float] = None,
) -> bool:
    """
    Poll until the device is paired (user tapped Trust) or the timeout elapses.

    Args:
        udid: Target device UDID.
        is_paired: Injectable pairing-status check (for tests).
        request_pairing: Injectable pairing trigger (for tests).
        timeout: Seconds to wait; defaults to settings.TRUST_PROMPT_TIMEOUT_SECONDS.

    Returns:
        True if pairing succeeded, False on timeout.
    """
    deadline = time.monotonic() + (timeout if timeout is not None else settings.TRUST_PROMPT_TIMEOUT_SECONDS)
    attempt_timeout = settings.PAIRING_ATTEMPT_TIMEOUT_SECONDS

    try:
        if await asyncio.wait_for(is_paired(udid), timeout=attempt_timeout):
            return True
    except Exception:  # pylint: disable=broad-except
        _logger.debug("pairing: initial is_paired check failed for udid=%s", udid)

    while time.monotonic() < deadline:
        try:
            if await asyncio.wait_for(request_pairing(udid), timeout=attempt_timeout):
                return True
        except Exception:  # pylint: disable=broad-except
            _logger.debug("pairing: request_pairing attempt failed for udid=%s, retrying", udid)
        await asyncio.sleep(1.0)

    _logger.warning("pairing: timed out waiting for trust udid=%s", udid)
    return False
