"""Pydantic request/response/event schemas for IPC payloads (docs/ipc_protocol.md)."""
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
