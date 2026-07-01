"""Live Photo pairing heuristic (spec FR-8, §9.4) — filename-stem convention."""
from app.db import get_session
from app.models import TransferItem
from app.services.live_photo import pair_live_photos


def _add_item(session, file_name: str) -> TransferItem:
    item = TransferItem(
        device_udid="udid-1",
        remote_path=f"/DCIM/100APPLE/{file_name}",
        file_name=file_name,
        remote_size_bytes=100,
    )
    session.add(item)
    return item


def test_pairs_matching_heic_and_mov():
    with get_session() as session:
        image = _add_item(session, "IMG_0001.HEIC")
        motion = _add_item(session, "IMG_0001.MOV")
        session.commit()
        session.refresh(image)
        session.refresh(motion)
        image_id, motion_id = image.id, motion.id

        linked = pair_live_photos(session, "udid-1")
        assert linked == 1

        session.refresh(image)
        session.refresh(motion)
        assert image.live_photo_pair_id == motion_id
        assert motion.live_photo_pair_id == image_id


def test_does_not_pair_unmatched_files():
    with get_session() as session:
        _add_item(session, "IMG_0002.HEIC")
        _add_item(session, "IMG_0003.MOV")
        session.commit()

        linked = pair_live_photos(session, "udid-1")
        assert linked == 0
