"""Library Cleanup scan engine (spec §11.4): walks local folders, hashes images in
parallel, and groups duplicates. Fully independent of the iPhone-transfer engine -
no AFC, no device_udid, no `devices`/`transfer_items` tables - operates on arbitrary
local folders the user points it at.
"""
import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import imagehash
from PIL import Image, UnidentifiedImageError
from sqlmodel import select

from app.config import settings
from app.db import get_session
from app.models import (
    DUPLICATE_GROUP_EXACT,
    DUPLICATE_GROUP_NEAR,
    LIBRARY_FILE_ACTIVE,
    DuplicateGroup,
    DuplicateGroupMember,
    LibraryFile,
)
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]


def is_delete_folder(path: Path) -> bool:
    """True if `path`'s name matches the safe-delete staging-folder convention (spec
    FR-L8) - e.g. "MyPictures-delete". Must be excluded from scanning, both as a scan
    root and as a discovered subfolder, or a rescan would recurse into the module's
    own staging area and re-flag already-moved files."""
    return path.name.endswith(settings.LIBRARY_DELETE_FOLDER_SUFFIX)


def _walk_image_files(root: Path) -> list[Path]:
    """Recursively collect image files under `root`, skipping any *-delete folder
    encountered anywhere in the tree (FR-L8) - not just at the root itself, since a
    -delete folder can exist as a sibling that a broader/parent scan later walks into."""
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            _logger.warning("library_scan: could not list directory %s", current, exc_info=True)
            continue
        for entry in entries:
            if entry.is_dir():
                if is_delete_folder(entry):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix.lower() in settings.LIBRARY_IMAGE_EXTENSIONS:
                out.append(entry)
    return out


def _hash_file(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Compute (content_hash, perceptual_hash) for one file. Called via
    asyncio.to_thread (see scan_library) - CPU-bound (image decode + hashing), must
    stay off the event loop thread. Returns None for a hash that couldn't be computed
    (corrupt file, unsupported format variant) rather than raising - one bad file must
    not abort the whole scan, mirroring the transfer engine's per-item failure
    isolation (backend/app/services/transfer_engine.py)."""
    content_hash = None
    try:
        sha256 = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha256.update(chunk)
        content_hash = sha256.hexdigest()
    except OSError:
        _logger.warning("library_scan: could not read %s for content hash", path, exc_info=True)
        return None, None

    perceptual_hash = None
    try:
        with Image.open(path) as img:
            perceptual_hash = str(imagehash.dhash(img))
    except (UnidentifiedImageError, OSError):
        _logger.warning("library_scan: could not decode %s for perceptual hash", path, exc_info=True)

    return content_hash, perceptual_hash


def _needs_hashing(existing: Optional[LibraryFile], size_bytes: int, modified_at: Optional[datetime]) -> bool:
    """False if `existing` already reflects this exact size+modified_at pair and a
    content hash was already computed - mirrors
    enumeration.py::requeue_missing_local_files's self-healing philosophy from the
    transfer engine, but in reverse (skip-if-unchanged rather than
    re-check-if-claimed-done), per spec FR-L4."""
    if existing is None:
        return True
    if existing.size_bytes != size_bytes or existing.modified_at != modified_at:
        return True
    return existing.content_hash is None


async def _process_file(path: Path, root: str) -> None:
    try:
        stat = path.stat()
    except OSError:
        _logger.warning("library_scan: could not stat %s", path, exc_info=True)
        return
    size_bytes = stat.st_size
    modified_at = datetime.fromtimestamp(stat.st_mtime)
    local_path = str(path)

    with get_session() as session:
        existing = session.exec(select(LibraryFile).where(LibraryFile.local_path == local_path)).first()
        needs_hash = _needs_hashing(existing, size_bytes, modified_at)

    content_hash: Optional[str] = None
    perceptual_hash: Optional[str] = None
    if needs_hash:
        content_hash, perceptual_hash = await asyncio.to_thread(_hash_file, path)

    with get_session() as session:
        # Re-fetch inside the write transaction - the hashing work above may have
        # taken a while (thousands of files across a worker pool), and another
        # concurrent _process_file call could have raced this same path in principle
        # (it can't in practice, since scan_library de-duplicates its file list from a
        # single walk, but re-fetching here is the same cheap-insurance pattern the
        # transfer engine uses throughout rather than trusting a stale in-memory read).
        existing = session.exec(select(LibraryFile).where(LibraryFile.local_path == local_path)).first()
        if existing is None:
            existing = LibraryFile(local_path=local_path, size_bytes=size_bytes, scan_root=root)
        existing.size_bytes = size_bytes
        existing.modified_at = modified_at
        existing.scan_root = root
        existing.last_scanned_at = datetime.now()
        existing.status = LIBRARY_FILE_ACTIVE
        if needs_hash:
            existing.content_hash = content_hash
            existing.perceptual_hash = perceptual_hash
        session.add(existing)
        session.commit()


async def scan_library(root: str, on_event: EventEmitter) -> tuple[int, int]:
    """Walk `root`, hash every image file (skipping unchanged ones from a prior
    scan), and (re-)form duplicate groups across the *entire* library scanned so far
    (not just this root - see _form_duplicate_groups). Returns
    (files_scanned, groups_found).

    Args:
        root: Local folder to scan. Must not itself be a *-delete staging folder.
        on_event: Async callback for `library_scan_progress` events.
    """
    root_path = Path(root)
    if is_delete_folder(root_path):
        # Scanning a staging folder directly makes no sense (FR-L8) - refuse rather
        # than silently doing something the user didn't intend.
        raise ValueError(f"{root} is a safe-delete staging folder and cannot be scanned directly")

    files = await asyncio.to_thread(_walk_image_files, root_path)
    total = len(files)
    _logger.info("library_scan: root=%s files=%s", root, total)

    semaphore = asyncio.Semaphore(settings.LIBRARY_SCAN_WORKER_CONCURRENCY)
    scanned = 0

    async def _bounded(path: Path) -> None:
        nonlocal scanned
        async with semaphore:
            await _process_file(path, root)
        scanned += 1
        if scanned % settings.LIBRARY_SCAN_PROGRESS_EVERY_N_FILES == 0 or scanned == total:
            await on_event("library_scan_progress", {"scanned": scanned, "total": total})

    await asyncio.gather(*(_bounded(p) for p in files))

    groups_found = await asyncio.to_thread(_form_duplicate_groups)
    return total, groups_found


def _form_duplicate_groups() -> int:
    """(Re-)form duplicate_groups/duplicate_group_members for every active, hashed
    LibraryFile - global across all previously-scanned folders, not scoped to a
    single scan_root, so a duplicate that exists *between* two separately-scanned
    folders is still found (e.g. the same photo copied into two different albums).
    Existing groups are cleared and rebuilt from scratch each time a scan completes -
    cheap (DB-only, no rehashing) and avoids subtle staleness from a prior scan's
    grouping surviving file moves/deletes/edits since.

    Near-duplicate grouping is a greedy single-pass clustering, O(n * existing
    clusters) - degrades toward O(n^2) in the worst case (no near-duplicates found at
    all). Fine for the thousands-of-files scale this module targets for v1; a
    bucketing/LSH strategy would be needed before this comfortably scales to tens of
    thousands - not yet validated against a real library that size (mirrors spec §9
    open question 5's "confirm empirically, don't guess" precedent for this exact
    class of performance question).
    """
    with get_session() as session:
        for member in session.exec(select(DuplicateGroupMember)).all():
            session.delete(member)
        for group in session.exec(select(DuplicateGroup)).all():
            session.delete(group)
        session.commit()

        files = session.exec(
            select(LibraryFile).where(
                LibraryFile.status == LIBRARY_FILE_ACTIVE,
                LibraryFile.content_hash.is_not(None),  # type: ignore[union-attr]
            )
        ).all()

        groups_created = 0
        grouped_ids: set[int] = set()

        by_content_hash: dict[str, list[LibraryFile]] = {}
        for f in files:
            assert f.content_hash is not None
            by_content_hash.setdefault(f.content_hash, []).append(f)

        for members in by_content_hash.values():
            if len(members) < 2:
                continue
            group = DuplicateGroup(group_type=DUPLICATE_GROUP_EXACT, created_at=datetime.now())
            session.add(group)
            session.flush()
            assert group.id is not None
            for member in members:
                assert member.id is not None
                session.add(DuplicateGroupMember(group_id=group.id, library_file_id=member.id))
                grouped_ids.add(member.id)
            groups_created += 1

        candidates = [f for f in files if f.id not in grouped_ids and f.perceptual_hash]
        clusters: list[list[LibraryFile]] = []
        for f in candidates:
            f_hash = imagehash.hex_to_hash(f.perceptual_hash)
            placed = False
            for cluster in clusters:
                assert cluster[0].perceptual_hash is not None
                rep_hash = imagehash.hex_to_hash(cluster[0].perceptual_hash)
                if f_hash - rep_hash <= settings.LIBRARY_NEAR_DUPLICATE_HAMMING_THRESHOLD:
                    cluster.append(f)
                    placed = True
                    break
            if not placed:
                clusters.append([f])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            assert cluster[0].perceptual_hash is not None
            rep_hash = imagehash.hex_to_hash(cluster[0].perceptual_hash)
            distances = []
            for m in cluster[1:]:
                assert m.perceptual_hash is not None
                distances.append(rep_hash - imagehash.hex_to_hash(m.perceptual_hash))
            avg_distance = sum(distances) / len(distances) if distances else 0.0
            group = DuplicateGroup(
                group_type=DUPLICATE_GROUP_NEAR,
                similarity_score=avg_distance,
                created_at=datetime.now(),
            )
            session.add(group)
            session.flush()
            assert group.id is not None
            for member in cluster:
                assert member.id is not None
                session.add(DuplicateGroupMember(group_id=group.id, library_file_id=member.id))
            groups_created += 1

        session.commit()
        return groups_created
