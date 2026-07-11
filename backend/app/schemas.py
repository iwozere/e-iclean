"""Pydantic request/response/event schemas for IPC payloads (docs/ipc_protocol.md)."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DeviceInfo(BaseModel):
    udid: str
    display_name: Optional[str] = None
    connected: bool = True


class EmptyParams(BaseModel):
    """Params model for methods that take no arguments (e.g. device.list)."""


# --- device.* ---

class DeviceListResult(BaseModel):
    devices: list[DeviceInfo]


class DeviceConnectParams(BaseModel):
    udid: str


class DeviceConnectResult(BaseModel):
    status: str  # connected | awaiting_trust | timed_out
    device: Optional[DeviceInfo] = None


# --- library.enumerate ---

class LibraryEnumerateParams(BaseModel):
    udid: str


class LibraryEnumerateResult(BaseModel):
    total_items: int
    total_bytes: int


# --- transfer.* ---

class TransferStartParams(BaseModel):
    udid: str
    destination: str


class TransferStartResult(BaseModel):
    session_id: int


class TransferSessionParams(BaseModel):
    session_id: int


class EmptyResult(BaseModel):
    pass


# --- verify.status ---

class VerifyStatusParams(BaseModel):
    session_id: int


class VerifyStatusResult(BaseModel):
    verified_count: int
    pending_count: int


# --- delete.batch ---

class DeleteBatchParams(BaseModel):
    item_ids: list[int]


class DeleteBatchFailure(BaseModel):
    item_id: int
    error_code: str


class DeleteBatchResult(BaseModel):
    deleted_count: int
    failures: list[DeleteBatchFailure] = []


# --- library_scan.* / library_delete.* (Library Cleanup module, spec §11) ---
# Fully independent of the transfer.*/delete.* schemas above - no udid anywhere here,
# this module operates on arbitrary local folders (spec §11.0).

class LibraryScanStartParams(BaseModel):
    root: str


class LibraryFileInfo(BaseModel):
    id: int
    local_path: str
    size_bytes: int
    modified_at: Optional[datetime] = None


class DuplicateGroupInfo(BaseModel):
    id: int
    group_type: str  # exact | near
    similarity_score: Optional[float] = None
    members: list[LibraryFileInfo]


class LibraryGroupsListResult(BaseModel):
    groups: list[DuplicateGroupInfo]


class LibraryDeleteBatchParams(BaseModel):
    library_file_ids: list[int]
    # Defaults to the safe move-to-<root>-delete-folder behavior (spec FR-L7) - the
    # UI's confirmation checkbox is checked (safe) by default, so its *unchecked*
    # state is what maps to permanent=True, an explicit opt-out.
    permanent: bool = False


class LibraryDeleteBatchFailure(BaseModel):
    library_file_id: int
    message: str


class LibraryDeleteBatchResult(BaseModel):
    deleted_count: int
    failures: list[LibraryDeleteBatchFailure] = []


# --- settings.* ---

class SettingsGetParams(BaseModel):
    pass


class SettingsGetResult(BaseModel):
    values: dict[str, Any]


class SettingsSetParams(BaseModel):
    values: dict[str, Any]


# --- Notifications (Python -> Rust, unsolicited) ---

class TransferProgressEvent(BaseModel):
    item_id: int
    bytes_transferred: int
    remote_size_bytes: int
    # Authoritative, DB-sourced sum of bytes_transferred across every item for this
    # device - not something the frontend can reconstruct itself from a running total
    # of just this event stream, since that resets to 0 on every app relaunch even
    # though the device/DB still remembers everything already copied (see
    # docs/DEVELOPMENT.md: this is why the progress bar showed 0%/650MB after a
    # restart when 52GB was already genuinely on disk from earlier sessions).
    device_bytes_transferred: int


class DeviceDisconnectedEvent(BaseModel):
    udid: str


class DeviceConnectedEvent(BaseModel):
    udid: str
    display_name: Optional[str] = None


class EnumerationProgressEvent(BaseModel):
    items_seen: int


class VerificationProgressEvent(BaseModel):
    item_id: int
    verified: bool


class DeleteProgressEvent(BaseModel):
    item_id: int
    deleted: bool


class LibraryScanProgressEvent(BaseModel):
    scanned: int
    total: int


class LibraryScanCompleteEvent(BaseModel):
    scanned_count: int
    group_count: int
