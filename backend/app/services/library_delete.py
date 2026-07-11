"""Library Cleanup delete service (spec §11.5): moves confirmed files to a sibling
`<root>-delete` staging folder by default (FR-L7), or permanently deletes them if the
user opts out via the checkbox. Local disk only - no AFC, no device, never touches
`transfer_items`/`devices` state (FR-L6).
"""
import shutil
from pathlib import Path
from typing import NamedTuple

from sqlmodel import select

from app.config import settings
from app.db import get_session
from app.models import (
    LIBRARY_FILE_ACTIVE,
    LIBRARY_FILE_DELETED,
    LIBRARY_FILE_MOVED_TO_DELETE_FOLDER,
    DuplicateGroup,
    DuplicateGroupMember,
    LibraryFile,
)
from app.utils.logger import setup_logger
from app.utils.naming import disambiguated_name

_logger = setup_logger(__name__)


class LibraryDeleteFailure(NamedTuple):
    library_file_id: int
    message: str


def delete_folder_for(scan_root: str) -> Path:
    """Sibling staging folder for a scan root, e.g. "MyPictures" ->
    "MyPictures-delete" (FR-L7) - suffix applied to the scanned root itself, never a
    subfolder, so the staging folder can be cleanly excluded from future scans by
    name (see `library_scan.is_delete_folder`)."""
    root_path = Path(scan_root)
    return root_path.parent / f"{root_path.name}{settings.LIBRARY_DELETE_FOLDER_SUFFIX}"


def _move_to_delete_folder(item: LibraryFile) -> str:
    """Move `item`'s file to its sibling `-delete` folder, preserving the path
    relative to `scan_root` (FR-L7). Returns the new local_path."""
    source = Path(item.local_path)
    relative = source.relative_to(Path(item.scan_root))
    destination = delete_folder_for(item.scan_root) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        # Collision (spec §11.5): reuse the transfer engine's disambiguation
        # convention (backend/app/utils/naming.py) rather than inventing a second
        # scheme for the same class of problem - fold the file's immediate parent
        # folder name into the filename.
        destination = destination.with_name(disambiguated_name(destination.name, str(source)))
        destination.parent.mkdir(parents=True, exist_ok=True)

    shutil.move(str(source), str(destination))
    return str(destination)


def delete_batch(library_file_ids: list[int], permanent: bool) -> tuple[int, list[LibraryDeleteFailure]]:
    """Move (default) or permanently delete the given library files.

    Args:
        library_file_ids: LibraryFile.id values to act on - always an explicit user
            selection (spec FR-L3), never inferred or pre-selected.
        permanent: If True, delete outright instead of moving to `<root>-delete`
            (FR-L7's explicit opt-out - the checkbox defaults to False/move).

    Returns:
        (succeeded_count, failures) - mirrors delete_service.py's batch-with-partial-
        failure-reporting shape (spec §5.6 point 7): one bad file must not abort the
        rest of the batch, and must never be silently treated as succeeded.
    """
    succeeded = 0
    failures: list[LibraryDeleteFailure] = []

    for file_id in library_file_ids:
        with get_session() as session:
            item = session.get(LibraryFile, file_id)
            if item is None:
                failures.append(LibraryDeleteFailure(file_id, "File record not found."))
                continue
            if item.status != LIBRARY_FILE_ACTIVE:
                # Already moved/deleted by an earlier call (e.g. a double-submitted
                # confirmation) - not an error exactly, but must not be silently
                # re-processed (the relative-path math below assumes an
                # still-under-scan_root local_path, which no longer holds once a file
                # has been moved).
                failures.append(LibraryDeleteFailure(file_id, "File was already moved or deleted."))
                continue
            try:
                if permanent:
                    Path(item.local_path).unlink()
                    item.status = LIBRARY_FILE_DELETED
                else:
                    item.local_path = _move_to_delete_folder(item)
                    item.status = LIBRARY_FILE_MOVED_TO_DELETE_FOLDER
                session.add(item)
                session.commit()
                succeeded += 1
            except OSError as exc:
                _logger.warning("library_delete: failed to process file_id=%s", file_id, exc_info=True)
                failures.append(LibraryDeleteFailure(file_id, str(exc)))
                continue

        _prune_resolved_groups(file_id)

    return succeeded, failures


def _prune_resolved_groups(acted_on_file_id: int) -> None:
    """A duplicate group with fewer than 2 remaining members is no longer a
    duplicate group. Cleans this up immediately rather than waiting for the user's
    next scan to rebuild groups from scratch (`library_scan._form_duplicate_groups`),
    so the review UI doesn't keep showing a stale single-member "group"."""
    with get_session() as session:
        memberships = session.exec(
            select(DuplicateGroupMember).where(DuplicateGroupMember.library_file_id == acted_on_file_id)
        ).all()
        for membership in memberships:
            group_id = membership.group_id
            session.delete(membership)
            session.commit()

            remaining = session.exec(
                select(DuplicateGroupMember).where(DuplicateGroupMember.group_id == group_id)
            ).all()
            if len(remaining) < 2:
                for leftover in remaining:
                    session.delete(leftover)
                group = session.get(DuplicateGroup, group_id)
                if group is not None:
                    session.delete(group)
                session.commit()
