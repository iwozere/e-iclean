"""Mitigate Windows USB selective suspend during transfers (spec §5.4).

Uses `powercfg` (bundled with every Windows install) rather than calling the Power
Management APIs directly — matches the "powercfg equivalent" phrasing in the spec, with
no extra native dependency. Falls back to a logged no-op if `powercfg` isn't available
(e.g. local dev on a non-Windows machine) rather than failing the transfer.
"""
import subprocess
from typing import Optional

from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

_USB_SELECTIVE_SUSPEND_SUBGROUP = "2a737441-1930-4402-8d77-b2bebba308a3"
_USB_SELECTIVE_SUSPEND_SETTING = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

_previous_ac_value: Optional[str] = None
_previous_dc_value: Optional[str] = None


def disable_usb_selective_suspend() -> None:
    """Disable USB selective suspend for the active power scheme, remembering the
    previous values so `restore_usb_selective_suspend` can put them back."""
    global _previous_ac_value, _previous_dc_value
    try:
        _previous_ac_value = _query_value("AC")
        _previous_dc_value = _query_value("DC")
        _set_value("AC", "0")
        _set_value("DC", "0")
        _apply()
        _logger.info("usb_power: selective suspend disabled for this session")
    except (OSError, subprocess.SubprocessError):
        _logger.warning("usb_power: could not toggle USB selective suspend (powercfg unavailable?)")


def restore_usb_selective_suspend() -> None:
    """Restore whatever USB selective suspend setting was in effect before
    `disable_usb_selective_suspend()` was called. No-op if that was never called."""
    if _previous_ac_value is None or _previous_dc_value is None:
        return
    try:
        _set_value("AC", _previous_ac_value)
        _set_value("DC", _previous_dc_value)
        _apply()
        _logger.info("usb_power: selective suspend setting restored")
    except (OSError, subprocess.SubprocessError):
        _logger.warning("usb_power: could not restore USB selective suspend setting")


def _query_value(mode: str) -> str:
    flag = "-getacvalueindex" if mode == "AC" else "-getdcvalueindex"
    out = subprocess.check_output(
        ["powercfg", flag, "SCHEME_CURRENT", _USB_SELECTIVE_SUSPEND_SUBGROUP, _USB_SELECTIVE_SUSPEND_SETTING],
        text=True,
    )
    return out.strip().split()[-1]


def _set_value(mode: str, value: str) -> None:
    flag = "-setacvalueindex" if mode == "AC" else "-setdcvalueindex"
    subprocess.check_call(
        ["powercfg", flag, "SCHEME_CURRENT", _USB_SELECTIVE_SUSPEND_SUBGROUP, _USB_SELECTIVE_SUSPEND_SETTING, value]
    )


def _apply() -> None:
    subprocess.check_call(["powercfg", "-setactive", "SCHEME_CURRENT"])
