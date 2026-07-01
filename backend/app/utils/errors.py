"""Plain-language error mapping (spec docs/project_specification.md §5.9).

Raw protocol/exception text must never reach the UI. Every failure mode that can
surface to the user goes through `AppError` with a stable `code` and a plain-language
`message`. The original exception text, if any, is preserved only in `detail` for the
"Copy diagnostic info" action and local log files.
"""
from __future__ import annotations

from typing import Optional


class AppError(Exception):
    """An error that is safe to show to the user, with an optional technical detail."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        """
        Serialize for the `error` field of an IPC response (docs/ipc_protocol.md).

        Returns:
            A dict with `code`, `message`, and optional `detail`.
        """
        payload = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


# Stable error codes. Add a new one here (not a generic fallback) for any new,
# anticipated failure mode — see AGENTS.md §7.
DEVICE_NOT_FOUND = "device_not_found"
DEVICE_DISCONNECTED = "device_disconnected"
PAIRING_TIMEOUT = "pairing_timeout"
PAIRING_REJECTED = "pairing_rejected"
AFC_TIMEOUT = "afc_timeout"
AFC_IO_ERROR = "afc_io_error"
DISK_FULL = "disk_full"
PERMISSION_DENIED = "permission_denied"
CHECKSUM_MISMATCH = "checksum_mismatch"
DELETE_NOT_VERIFIED = "delete_not_verified"
BACKEND_INTERNAL = "backend_internal"

_MESSAGES = {
    DEVICE_NOT_FOUND: "No iPhone was found connected to this PC.",
    DEVICE_DISCONNECTED: "Connection lost. Reconnect your iPhone to resume — no progress was lost.",
    PAIRING_TIMEOUT: "Your iPhone didn't respond to the trust request in time. Unlock it and try again.",
    PAIRING_REJECTED: "Trust was declined on the iPhone. Reconnect and tap \"Trust\" to continue.",
    AFC_TIMEOUT: "The iPhone stopped responding while transferring a file. Reconnect to resume.",
    AFC_IO_ERROR: "A file couldn't be read from the iPhone. It will be retried automatically.",
    DISK_FULL: "There isn't enough free space on this PC for the transfer.",
    PERMISSION_DENIED: "E-FileTrans isn't allowed to access that location. Check folder permissions.",
    CHECKSUM_MISMATCH: "A copied file didn't verify correctly and will be re-transferred.",
    DELETE_NOT_VERIFIED: "Files can only be deleted from the phone after they're verified.",
    BACKEND_INTERNAL: "Something went wrong. Check the log for details.",
}


def app_error(code: str, detail: Optional[str] = None) -> AppError:
    """
    Build an `AppError` for a known error code using its plain-language message.

    Args:
        code: One of the module-level error code constants.
        detail: Optional technical detail (exception text) for diagnostics only.

    Returns:
        A populated `AppError`.
    """
    message = _MESSAGES.get(code, _MESSAGES[BACKEND_INTERNAL])
    return AppError(code=code, message=message, detail=detail)
