"""Library Cleanup scan engine (spec §11.4): folder walk with *-delete exclusion,
content + perceptual hashing, skip-unchanged, and duplicate grouping."""
import random
from pathlib import Path

from PIL import Image

from app.db import get_session
from app.models import DUPLICATE_GROUP_NEAR, DuplicateGroup, DuplicateGroupMember, LibraryFile
from app.services.library_scan import is_delete_folder, scan_library
from sqlmodel import select


def _make_random_image(path: Path, seed: int) -> None:
    """A pseudo-random noise image seeded by `seed`. dhash compares adjacent-pixel
    brightness gradients, so different seeds reliably produce very different
    perceptual hashes (empirically confirmed: diff ~32 out of 64 bits) - unlike a
    fixed pattern with only different colors, which dhash barely distinguishes."""
    rng = random.Random(seed)
    img = Image.new("RGB", (64, 64))
    pixels = img.load()
    for x in range(64):
        for y in range(64):
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    img.save(path)


def _make_near_duplicate(path: Path, source_path: Path, perturb_pixels: int = 3) -> None:
    """A near-duplicate of the image at `source_path`: same content with a handful of
    pixels changed - close enough to stay under
    settings.LIBRARY_NEAR_DUPLICATE_HAMMING_THRESHOLD (empirically confirmed: diff ~2)
    but not byte-identical, simulating e.g. a re-save/re-compress by a different app."""
    img = Image.open(source_path).convert("RGB")
    pixels = img.load()
    rng = random.Random(999)
    for _ in range(perturb_pixels):
        x, y = rng.randrange(64), rng.randrange(64)
        pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    img.save(path)


async def test_scan_finds_exact_duplicate_group(tmp_path, events):
    root = tmp_path / "MyPictures"
    (root / "sub").mkdir(parents=True)
    _make_random_image(root / "a.jpg", 1)
    _make_random_image(root / "b.jpg", 200)
    # Exact duplicate: identical bytes, different path.
    (root / "sub" / "a_copy.jpg").write_bytes((root / "a.jpg").read_bytes())

    total, groups = await scan_library(str(root), events)

    assert total == 3
    assert groups == 1

    with get_session() as session:
        group = session.exec(select(DuplicateGroup)).one()
        assert group.group_type == "exact"
        members = session.exec(
            select(DuplicateGroupMember).where(DuplicateGroupMember.group_id == group.id)
        ).all()
        assert len(members) == 2


async def test_scan_finds_near_duplicate_group(tmp_path, events):
    """spec FR-L2: a re-saved/re-compressed copy (different bytes, visually near-
    identical) must be grouped as a near-duplicate, not missed entirely."""
    root = tmp_path / "MyPictures"
    root.mkdir()
    _make_random_image(root / "a.jpg", 1)
    _make_near_duplicate(root / "a_edited.jpg", root / "a.jpg")
    _make_random_image(root / "unrelated.jpg", 500)

    total, groups = await scan_library(str(root), events)

    assert total == 3
    assert groups == 1

    with get_session() as session:
        group = session.exec(select(DuplicateGroup)).one()
        assert group.group_type == DUPLICATE_GROUP_NEAR
        assert group.similarity_score is not None
        members = session.exec(
            select(DuplicateGroupMember).where(DuplicateGroupMember.group_id == group.id)
        ).all()
        assert len(members) == 2


async def test_scan_does_not_group_distinct_files(tmp_path, events):
    root = tmp_path / "MyPictures"
    root.mkdir()
    _make_random_image(root / "a.jpg", 1)
    _make_random_image(root / "b.jpg", 200)

    total, groups = await scan_library(str(root), events)

    assert total == 2
    assert groups == 0


async def test_scan_excludes_delete_folder_as_subfolder(tmp_path, events):
    """A *-delete folder anywhere in the tree must be skipped (spec FR-L8), not just
    when it's the scan root itself - otherwise a parent-folder scan would recurse
    into the module's own staging area and re-flag already-moved files."""
    root = tmp_path / "MyPictures"
    root.mkdir()
    delete_folder = tmp_path / "MyPictures-delete"
    delete_folder.mkdir()
    _make_random_image(root / "a.jpg", 1)
    _make_random_image(delete_folder / "already_moved.jpg", 42)

    total, _ = await scan_library(str(tmp_path), events)

    assert total == 1


async def test_scan_refuses_to_scan_a_delete_folder_directly(tmp_path, events):
    delete_folder = tmp_path / "MyPictures-delete"
    delete_folder.mkdir()

    try:
        await scan_library(str(delete_folder), events)
        assert False, "expected ValueError"
    except ValueError:
        pass


async def test_is_delete_folder():
    assert is_delete_folder(Path("D:/Photos/MyPictures-delete"))
    assert not is_delete_folder(Path("D:/Photos/MyPictures"))


async def test_rescan_skips_unchanged_file_hash(tmp_path, events, monkeypatch):
    """spec FR-L4: a file whose path+size+modified-time haven't changed since a prior
    scan must not be rehashed."""
    root = tmp_path / "MyPictures"
    root.mkdir()
    _make_random_image(root / "a.jpg", 1)

    await scan_library(str(root), events)

    calls = []
    import app.services.library_scan as scan_module

    original = scan_module._hash_file

    def _tracking_hash_file(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(scan_module, "_hash_file", _tracking_hash_file)

    await scan_library(str(root), events)

    assert calls == []


async def test_rescan_rehashes_changed_file(tmp_path, events):
    """A file whose content changed (different size) since the prior scan must be
    rehashed - the opposite case from the skip-unchanged test above."""
    root = tmp_path / "MyPictures"
    root.mkdir()
    _make_random_image(root / "a.jpg", 1)
    await scan_library(str(root), events)

    with get_session() as session:
        before = session.exec(select(LibraryFile)).one()
        original_hash = before.content_hash

    _make_random_image(root / "a.jpg", 250)  # overwrite with different content
    await scan_library(str(root), events)

    with get_session() as session:
        after = session.exec(select(LibraryFile)).one()
        assert after.content_hash != original_hash


async def test_scan_progress_events_emitted(tmp_path):
    root = tmp_path / "MyPictures"
    root.mkdir()
    for i in range(3):
        _make_random_image(root / f"img{i}.jpg", i + 1)

    collected = []

    async def emit(event, data):
        collected.append((event, data))

    await scan_library(str(root), emit)

    progress_events = [e for e in collected if e[0] == "library_scan_progress"]
    assert progress_events
    assert progress_events[-1][1]["scanned"] == 3
    assert progress_events[-1][1]["total"] == 3
