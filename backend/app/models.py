"""SQLModel ORM models — mirrors docs/project_specification.md §5.3 exactly."""
from datetime import datetime
from typing import Optional, Any

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
    __tablename__: Any = "devices"

    udid: str = Field(primary_key=True)
    display_name: Optional[str] = None
    last_connected_at: Optional[datetime] = None


class TransferItem(SQLModel, table=True):
    __tablename__: Any = "transfer_items"
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
    __tablename__: Any = "transfer_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    device_udid: str = Field(foreign_key="devices.udid")
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    total_files: Optional[int] = None
    total_bytes: Optional[int] = None
    outcome: Optional[str] = None  # completed | interrupted | cancelled


# Fixed status values for library_files.status (spec §11.3). Never invent ad-hoc
# strings - mirrors the transfer_items.status convention above.
LIBRARY_FILE_ACTIVE = "active"
LIBRARY_FILE_MOVED_TO_DELETE_FOLDER = "moved_to_delete_folder"
LIBRARY_FILE_DELETED = "deleted"

# Duplicate group classification (spec §11.3).
DUPLICATE_GROUP_EXACT = "exact"
DUPLICATE_GROUP_NEAR = "near"


class LibraryFile(SQLModel, table=True):
    """A file discovered by a Library Cleanup scan (spec §11.3) - fully independent
    of the iPhone-transfer data model above (no device_udid anywhere here); this
    module operates on arbitrary local folders."""

    __tablename__: Any = "library_files"

    id: Optional[int] = Field(default=None, primary_key=True)
    local_path: str = Field(unique=True)
    size_bytes: int
    modified_at: Optional[datetime] = None
    content_hash: Optional[str] = None  # sha256, populated after hashing
    perceptual_hash: Optional[str] = None  # e.g. 64-bit dHash, hex-encoded
    last_scanned_at: Optional[datetime] = None
    status: str = Field(default=LIBRARY_FILE_ACTIVE)
    # The scan root this file was discovered under - lets the scan-exclusion rule
    # (spec FR-L8) and the delete-folder relative-path move (FR-L7) both be computed
    # without re-deriving it from local_path.
    scan_root: str


class DuplicateGroup(SQLModel, table=True):
    __tablename__: Any = "duplicate_groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    group_type: str  # exact | near
    similarity_score: Optional[float] = None  # NULL for exact groups
    created_at: Optional[datetime] = None


class DuplicateGroupMember(SQLModel, table=True):
    __tablename__: Any = "duplicate_group_members"

    group_id: int = Field(foreign_key="duplicate_groups.id", primary_key=True)
    library_file_id: int = Field(foreign_key="library_files.id", primary_key=True)


class Setting(SQLModel, table=True):
    """Persisted key/value app settings (spec §5.7 points 3, 8 - destination folder
    default, concurrency toggle). Not in the spec's §5.3 schema listing, which predates
    this being wired up; added as a minimal key/value table rather than a dedicated
    column per setting since MVP only needs a handful of loosely-typed values."""

    __tablename__: Any = "settings"

    key: str = Field(primary_key=True)
    value: str
