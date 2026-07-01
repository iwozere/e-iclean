"""SQLModel ORM models — mirrors docs/project_specification.md §5.3 exactly."""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

# Fixed status values for transfer_items.status. Never invent ad-hoc strings.
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_PARTIAL = "partial"
STATUS_COPIED = "copied"
STATUS_VERIFIED = "verified"
STATUS_DELETE_PENDING = "delete_pending"
STATUS_DELETED = "deleted"
STATUS_FAILED = "failed"

ALL_STATUSES = {
    STATUS_PENDING,
    STATUS_IN_PROGRESS,
    STATUS_PARTIAL,
    STATUS_COPIED,
    STATUS_VERIFIED,
    STATUS_DELETE_PENDING,
    STATUS_DELETED,
    STATUS_FAILED,
}

# Statuses that must hold before a file is eligible for deletion from the device.
DELETE_ELIGIBLE_STATUSES = {STATUS_VERIFIED}


class Device(SQLModel, table=True):
    __tablename__ = "devices"

    udid: str = Field(primary_key=True)
    display_name: Optional[str] = None
    last_connected_at: Optional[datetime] = None


class TransferItem(SQLModel, table=True):
    __tablename__ = "transfer_items"
    __table_args__ = (UniqueConstraint("device_udid", "remote_path"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    device_udid: str = Field(foreign_key="devices.udid")
    remote_path: str
    file_name: str
    remote_size_bytes: int
    remote_modified_at: Optional[datetime] = None
    local_path: Optional[str] = None
    bytes_transferred: int = Field(default=0)
    status: str = Field(default=STATUS_PENDING)
    checksum_local: Optional[str] = None
    live_photo_pair_id: Optional[int] = Field(default=None, foreign_key="transfer_items.id")
    error_message: Optional[str] = None
    last_attempt_at: Optional[datetime] = None


class TransferSession(SQLModel, table=True):
    __tablename__ = "transfer_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    device_udid: str = Field(foreign_key="devices.udid")
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    total_files: Optional[int] = None
    total_bytes: Optional[int] = None
    outcome: Optional[str] = None  # completed | interrupted | cancelled


class Setting(SQLModel, table=True):
    """Persisted key/value app settings (spec §5.7 points 3, 8 - destination folder
    default, concurrency toggle). Not in the spec's §5.3 schema listing, which predates
    this being wired up; added as a minimal key/value table rather than a dedicated
    column per setting since MVP only needs a handful of loosely-typed values."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str
