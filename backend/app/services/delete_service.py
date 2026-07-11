"""Batched, verify-gated delete from the device (spec §5.6 points 4-7, §7).

Delete is always an explicit, separate user action. Every path here refuses to touch
anything that isn't `verified`, and never splits a Live Photo pair (FR-8): eligibility
is checked per pair-group before any AFC `remove` call is issued, so a group is either
deleted in full or not attempted at all.
"""
import asyncio
from typing import Awaitable, Callable

from app.config import settings
from app.db import get_session
from app.device.afc_client import AfcClient
from app.models import DELETE_ELIGIBLE_STATUSES, STATUS_DELETED, TransferItem
from app.schemas import DeleteBatchFailure, DeleteProgressEvent
from app.utils.errors import AFC_IO_ERROR, DELETE_NOT_VERIFIED, app_error
from app.utils.logger import setup_logger

_logger = setup_logger(__name__)

EventEmitter = Callable[[str, object], Awaitable[None]]


async def delete_batch(
    item_ids: list[int], afc: AfcClient, on_event: EventEmitter
) -> tuple[int, list[DeleteBatchFailure]]:
    """
    Delete a user-confirmed batch of items from the device.

    Args:
        item_ids: Items to delete, as confirmed by the user in the UI.
        afc: Connected AFC client for the owning device.
        on_event: Notification emitter for per-item delete_progress events.

    Returns:
        (deleted_count, failures) — a pair-group that isn't fully `verified` produces
        exactly one failure entry keyed to the originally requested item id, and none
        of its members are touched. Failed AFC removals are reported per member and
        left as `verified` (not `deleted`), so they're retryable (spec §5.6 point 7).
    """
    to_delete: list[int] = []
    failures: list[DeleteBatchFailure] = []

    with get_session() as session:
        already_grouped: set[int] = set()
        for requested_id in item_ids:
            if requested_id in already_grouped:
                continue

            item = session.get(TransferItem, requested_id)
            if item is None:
                failures.append(DeleteBatchFailure(item_id=requested_id, error_code=DELETE_NOT_VERIFIED))
                continue

            assert item.id is not None
            member_ids: list[int] = [item.id]
            if item.live_photo_pair_id is not None:
                member_ids.append(item.live_photo_pair_id)
            already_grouped.update(member_ids)

            if not _group_is_delete_eligible(session, member_ids):
                _logger.warning(
                    "delete_service: refused non-eligible group requested_id=%s members=%s",
                    requested_id,
                    member_ids,
                )
                failures.append(DeleteBatchFailure(item_id=requested_id, error_code=DELETE_NOT_VERIFIED))
                continue

            to_delete.extend(member_ids)

    deleted_count = 0
    for batch_start in range(0, len(to_delete), settings.DELETE_BATCH_SIZE):
        batch = to_delete[batch_start : batch_start + settings.DELETE_BATCH_SIZE]
        for member_id in batch:
            ok, error_code = await asyncio.to_thread(_delete_one, member_id, afc)
            if ok:
                deleted_count += 1
            else:
                failures.append(DeleteBatchFailure(item_id=member_id, error_code=error_code))
            await on_event("delete_progress", DeleteProgressEvent(item_id=member_id, deleted=ok))

    _logger.info("delete_service: deleted=%s failed=%s", deleted_count, len(failures))
    return deleted_count, failures


def _group_is_delete_eligible(session, member_ids: list[int]) -> bool:
    for member_id in member_ids:
        member = session.get(TransferItem, member_id)
        if member is None or member.status not in DELETE_ELIGIBLE_STATUSES:
            return False
    return True


def _delete_one(item_id: int, afc: AfcClient) -> tuple[bool, str]:
    with get_session() as session:
        item = session.get(TransferItem, item_id)
        if item is None:
            return False, DELETE_NOT_VERIFIED

        # Defense-in-depth: the caller already validated the whole pair-group as
        # eligible, but re-check here since this runs as a separate DB transaction.
        if item.status not in DELETE_ELIGIBLE_STATUSES:
            _logger.warning("delete_service: refused non-verified item_id=%s status=%s", item_id, item.status)
            return False, DELETE_NOT_VERIFIED

        try:
            afc.remove(item.remote_path)
        except Exception as exc:  # pylint: disable=broad-except
            _logger.exception("delete_service: AFC remove failed item_id=%s", item_id)
            return False, app_error(AFC_IO_ERROR, detail=str(exc)).code

        item.status = STATUS_DELETED
        session.add(item)
        session.commit()
        return True, ""
