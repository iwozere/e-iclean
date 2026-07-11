"""Library Cleanup safe-delete service (spec §11.5): move-to-<root>-delete-folder
(default) vs. permanent delete, collision handling, and duplicate-group pruning."""
from pathlib import Path

from app.db import get_session
from app.models import (
    LIBRARY_FILE_ACTIVE,
    LIBRARY_FILE_DELETED,
    LIBRARY_FILE_MOVED_TO_DELETE_FOLDER,
    DuplicateGroup,
    DuplicateGroupMember,
    LibraryFile,
)
from app.services.library_delete import delete_batch, delete_folder_for
from sqlmodel import select


def _make_library_file(local_path: Path, scan_root: Path, content: bytes = b"data") -> int:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)
    with get_session() as session:
        item = LibraryFile(
            local_path=str(local_path),
            size_bytes=len(content),
            scan_root=str(scan_root),
            status=LIBRARY_FILE_ACTIVE,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        return item.id


def test_move_preserves_relative_path_under_delete_folder(tmp_path):
    root = tmp_path / "MyPictures"
    file_id = _make_library_file(root / "2020" / "Summer" / "IMG_001.jpg", root)

    succeeded, failures = delete_batch([file_id], permanent=False)

    assert succeeded == 1
    assert failures == []
    expected = tmp_path / "MyPictures-delete" / "2020" / "Summer" / "IMG_001.jpg"
    assert expected.exists()
    assert not (root / "2020" / "Summer" / "IMG_001.jpg").exists()

    with get_session() as session:
        item = session.get(LibraryFile, file_id)
        assert item is not None
        assert item.status == LIBRARY_FILE_MOVED_TO_DELETE_FOLDER
        assert item.local_path == str(expected)


def test_permanent_delete_removes_file_outright(tmp_path):
    root = tmp_path / "MyPictures"
    file_id = _make_library_file(root / "IMG_002.jpg", root)
    original_path = root / "IMG_002.jpg"

    succeeded, failures = delete_batch([file_id], permanent=True)

    assert succeeded == 1
    assert failures == []
    assert not original_path.exists()
    delete_folder = delete_folder_for(str(root))
    assert not delete_folder.exists()

    with get_session() as session:
        item = session.get(LibraryFile, file_id)
        assert item is not None
        assert item.status == LIBRARY_FILE_DELETED


def test_move_collision_disambiguates_with_parent_folder_suffix(tmp_path):
    """spec §11.5: reuses the transfer engine's disambiguation convention rather than
    inventing a new one - if the target path in the delete folder already exists, fold
    a distinguishing parent-folder-name suffix into the filename."""
    root = tmp_path / "MyPictures"
    delete_folder = tmp_path / "MyPictures-delete"
    (delete_folder / "Trip").mkdir(parents=True)
    (delete_folder / "Trip" / "IMG_001.jpg").write_bytes(b"already-here")

    file_id = _make_library_file(root / "Trip" / "IMG_001.jpg", root, content=b"new-file")

    succeeded, failures = delete_batch([file_id], permanent=False)

    assert succeeded == 1
    assert failures == []
    # Original collision target untouched, new file landed at a disambiguated path.
    assert (delete_folder / "Trip" / "IMG_001.jpg").read_bytes() == b"already-here"
    disambiguated = delete_folder / "Trip" / "IMG_001 (Trip).jpg"
    assert disambiguated.exists()
    assert disambiguated.read_bytes() == b"new-file"


def test_delete_batch_reports_failure_for_missing_record():
    succeeded, failures = delete_batch([999999], permanent=False)

    assert succeeded == 0
    assert len(failures) == 1
    assert failures[0].library_file_id == 999999


def test_delete_batch_refuses_to_reprocess_non_active_item(tmp_path):
    root = tmp_path / "MyPictures"
    file_id = _make_library_file(root / "IMG_003.jpg", root)
    delete_batch([file_id], permanent=False)  # first call: move succeeds

    succeeded, failures = delete_batch([file_id], permanent=False)  # second call on the same id

    assert succeeded == 0
    assert len(failures) == 1
    assert failures[0].library_file_id == file_id


def test_delete_prunes_group_with_fewer_than_two_remaining_members(tmp_path):
    root = tmp_path / "MyPictures"
    file_a_id = _make_library_file(root / "a.jpg", root)
    file_b_id = _make_library_file(root / "b.jpg", root)

    with get_session() as session:
        group = DuplicateGroup(group_type="exact")
        session.add(group)
        session.flush()
        assert group.id is not None
        group_id = group.id
        session.add(DuplicateGroupMember(group_id=group_id, library_file_id=file_a_id))
        session.add(DuplicateGroupMember(group_id=group_id, library_file_id=file_b_id))
        session.commit()

    delete_batch([file_a_id], permanent=True)

    with get_session() as session:
        assert session.get(DuplicateGroup, group_id) is None
        remaining_members = session.exec(
            select(DuplicateGroupMember).where(DuplicateGroupMember.group_id == group_id)
        ).all()
        assert remaining_members == []
