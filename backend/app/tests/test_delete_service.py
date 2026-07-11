"""Delete safety: verify-before-delete gate and Live Photo pair guard (spec §5.6, §7).

These are the hard safety-requirement tests — see AGENTS.md §15 item 9.
"""
from app.db import get_session
from app.device.afc_client import MockAfcClient
from app.models import STATUS_COPIED, STATUS_DELETED, STATUS_VERIFIED, TransferItem
from app.services.delete_service import delete_batch


def _add_item(session, file_name: str, status: str) -> TransferItem:
    item = TransferItem(
        device_udid="udid-1",
        remote_path=f"/DCIM/100APPLE/{file_name}",
        file_name=file_name,
        remote_size_bytes=10,
        status=status,
    )
    session.add(item)
    return item


async def test_refuses_to_delete_unverified_item(events):
    with get_session() as session:
        item = _add_item(session, "IMG_0001.HEIC", STATUS_COPIED)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        item_id = item.id

    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0001.HEIC": b"x" * 10})
    deleted_count, failures = await delete_batch([item_id], afc, events)

    assert deleted_count == 0
    assert len(failures) == 1
    assert failures[0].item_id == item_id

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item is not None
        assert item.status == STATUS_COPIED  # untouched, not silently marked deleted


async def test_deletes_verified_item(events):
    remote_path = "/DCIM/100APPLE/IMG_0002.HEIC"
    with get_session() as session:
        item = _add_item(session, "IMG_0002.HEIC", STATUS_VERIFIED)
        session.commit()
        session.refresh(item)
        assert item.id is not None
        item_id = item.id

    afc = MockAfcClient({remote_path: b"x" * 10})
    deleted_count, failures = await delete_batch([item_id], afc, events)

    assert deleted_count == 1
    assert failures == []

    with get_session() as session:
        item = session.get(TransferItem, item_id)
        assert item is not None
        assert item.status == STATUS_DELETED


async def test_refuses_to_split_live_photo_pair(events):
    """Deleting the image half must not proceed unless the motion half is also
    verified — FR-8 hard requirement."""
    with get_session() as session:
        image = _add_item(session, "IMG_0003.HEIC", STATUS_VERIFIED)
        motion = _add_item(session, "IMG_0003.MOV", STATUS_COPIED)  # not yet verified
        session.add(image)
        session.add(motion)
        session.commit()
        session.refresh(image)
        session.refresh(motion)
        image.live_photo_pair_id = motion.id
        motion.live_photo_pair_id = image.id
        session.add(image)
        session.add(motion)
        session.commit()
        assert image.id is not None
        image_id = image.id

    afc = MockAfcClient(
        {"/DCIM/100APPLE/IMG_0003.HEIC": b"x" * 10, "/DCIM/100APPLE/IMG_0003.MOV": b"y" * 10}
    )
    deleted_count, failures = await delete_batch([image_id], afc, events)

    assert deleted_count == 0
    assert len(failures) == 1

    with get_session() as session:
        image = session.get(TransferItem, image_id)
        assert image is not None
        assert image.status == STATUS_VERIFIED  # not deleted


async def test_deletes_both_halves_of_verified_live_photo_pair(events):
    with get_session() as session:
        image = _add_item(session, "IMG_0004.HEIC", STATUS_VERIFIED)
        motion = _add_item(session, "IMG_0004.MOV", STATUS_VERIFIED)
        session.add(image)
        session.add(motion)
        session.commit()
        session.refresh(image)
        session.refresh(motion)
        image.live_photo_pair_id = motion.id
        motion.live_photo_pair_id = image.id
        session.add(image)
        session.add(motion)
        session.commit()
        assert image.id is not None
        assert motion.id is not None
        image_id = image.id
        motion_id = motion.id

    afc = MockAfcClient(
        {"/DCIM/100APPLE/IMG_0004.HEIC": b"x" * 10, "/DCIM/100APPLE/IMG_0004.MOV": b"y" * 10}
    )
    # Only the image half is explicitly requested — the pair must be expanded so
    # both are deleted together, never split (FR-8).
    deleted_count, failures = await delete_batch([image_id], afc, events)

    assert deleted_count == 2
    assert failures == []

    with get_session() as session:
        image_item = session.get(TransferItem, image_id)
        motion_item = session.get(TransferItem, motion_id)
        assert image_item is not None
        assert motion_item is not None
        assert image_item.status == STATUS_DELETED
        assert motion_item.status == STATUS_DELETED
