"""Live Photo pairing heuristic (spec FR-8, open question §9.4).

MVP heuristic: pair items in the same device library whose filename stem matches and
whose extensions are an image/video pair (HEIC|JPG + MOV) — a filename convention, not
a metadata-based signal. Apple's actual Live Photo asset identifier (in EXIF / QuickTime
content-identifier metadata) is the more robust long-term signal. This is flagged
explicitly per spec §9 as needing empirical confirmation across iOS versions before this
guarantee is relied on for the delete-safety requirement (FR-8) at scale.
"""
from sqlmodel import Session, select

from app.models import TransferItem

_IMAGE_EXTENSIONS = {".heic", ".jpg", ".jpeg"}
_MOTION_EXTENSIONS = {".mov"}


def _stem_and_ext(file_name: str) -> tuple[str, str]:
    if "." not in file_name:
        return file_name, ""
    stem, _, ext = file_name.rpartition(".")
    return stem, f".{ext.lower()}"


def pair_live_photos(session: Session, device_udid: str) -> int:
    """
    Find and link unlinked Live Photo image/motion pairs for a device.

    Args:
        session: Active DB session; caller is responsible for committing afterward
            (this function commits internally if it makes any changes).
        device_udid: Device to scan.

    Returns:
        Number of pairs newly linked.
    """
    items = session.exec(
        select(TransferItem).where(
            TransferItem.device_udid == device_udid,
            TransferItem.live_photo_pair_id.is_(None),  # type: ignore[union-attr]
        )
    ).all()

    by_stem: dict[str, list[TransferItem]] = {}
    for item in items:
        stem = _stem_and_ext(item.file_name)[0]
        by_stem.setdefault(stem, []).append(item)

    linked = 0
    for group in by_stem.values():
        images = [i for i in group if _stem_and_ext(i.file_name)[1] in _IMAGE_EXTENSIONS]
        motions = [i for i in group if _stem_and_ext(i.file_name)[1] in _MOTION_EXTENSIONS]
        if len(images) == 1 and len(motions) == 1:
            images[0].live_photo_pair_id = motions[0].id
            motions[0].live_photo_pair_id = images[0].id
            session.add(images[0])
            session.add(motions[0])
            linked += 1

    if linked:
        session.commit()
    return linked
