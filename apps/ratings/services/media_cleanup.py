from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from ..media_storage import MediaStorageGateway, get_media_storage_gateway
from ..models import MediaAttachment
from .media_uploads import MediaUploadStorageError, MediaUploadValidationError

logger = logging.getLogger(__name__)

_UNATTACHED_STATUSES = (
    MediaAttachment.Status.PENDING,
    MediaAttachment.Status.FINALIZING,
    MediaAttachment.Status.RECLAIMING,
    MediaAttachment.Status.READY,
    MediaAttachment.Status.DELETING,
)


@dataclass(frozen=True, slots=True)
class ExpiredMediaCleanupResult:
    scanned: int
    deleted: int
    failed: int


@dataclass(frozen=True, slots=True)
class _ExpiredMediaCleanupClaim:
    upload_id: UUID
    expires_at: datetime
    object_keys: tuple[str, ...]


def expired_media_upload_count(
    *,
    cutoff: datetime | None = None,
    uploader_id: int | None = None,
) -> int:
    effective_cutoff = cutoff or timezone.now()
    uploads = MediaAttachment.objects.filter(
        status__in=_UNATTACHED_STATUSES,
        expires_at__lte=effective_cutoff,
    )
    if uploader_id is not None:
        uploads = uploads.filter(uploader_id=uploader_id)
    return uploads.count()


@transaction.atomic(durable=True)
def _claim_expired_media_cleanup(
    *,
    upload_id: UUID,
    cutoff: datetime,
) -> _ExpiredMediaCleanupClaim | None:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(
            pk=upload_id,
            status__in=_UNATTACHED_STATUSES,
            expires_at__lte=cutoff,
        )
    except MediaAttachment.DoesNotExist:
        return None

    # Commit this tombstone before any external delete. It invalidates active
    # finalizers and preserves enough information to retry partial R2 cleanup.
    if attachment.status != MediaAttachment.Status.DELETING:
        attachment.status = MediaAttachment.Status.DELETING
        attachment.save(update_fields=("status",))

    # Completion only deletes the reusable staging object on a best-effort
    # basis, so every retry must retain its canonical key independently of the
    # immutable object key stored on a READY or DELETING attachment.
    object_keys = [f"pending/{attachment.pk}", attachment.object_key]
    if attachment.finalization_token is not None:
        token_key = f"media/{attachment.pk}/{attachment.finalization_token}"
        object_keys.append(token_key)
    return _ExpiredMediaCleanupClaim(
        upload_id=attachment.pk,
        expires_at=attachment.expires_at,
        object_keys=tuple(dict.fromkeys(object_keys)),
    )


@transaction.atomic(durable=True)
def _finish_expired_media_cleanup(*, claim: _ExpiredMediaCleanupClaim) -> bool:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(
            pk=claim.upload_id,
            status=MediaAttachment.Status.DELETING,
            expires_at=claim.expires_at,
        )
    except MediaAttachment.DoesNotExist:
        return False
    attachment.delete()
    return True


def cleanup_expired_media_uploads(
    *,
    limit: int = 100,
    cutoff: datetime | None = None,
    storage: MediaStorageGateway | None = None,
    uploader_id: int | None = None,
    upload_ids: Sequence[UUID] | None = None,
) -> ExpiredMediaCleanupResult:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise MediaUploadValidationError("정리 개수는 1 이상이어야 해요.")

    effective_cutoff = cutoff or timezone.now()
    try:
        storage_gateway = storage or get_media_storage_gateway()
    except Exception as error:
        raise MediaUploadStorageError("파일 저장소에 연결하지 못했어요.") from error

    uploads = MediaAttachment.objects.filter(
        status__in=_UNATTACHED_STATUSES,
        expires_at__lte=effective_cutoff,
    )
    if uploader_id is not None:
        uploads = uploads.filter(uploader_id=uploader_id)
    if upload_ids is not None:
        uploads = uploads.filter(pk__in=tuple(upload_ids))
    expired_upload_ids = tuple(
        uploads.order_by("expires_at", "created_at", "pk").values_list("pk", flat=True)[
            :limit
        ]
    )
    deleted = 0
    failed = 0
    for upload_id in expired_upload_ids:
        try:
            claim = _claim_expired_media_cleanup(
                upload_id=upload_id,
                cutoff=effective_cutoff,
            )
            if claim is None:
                continue
            for object_key in claim.object_keys:
                storage_gateway.delete_object(object_key=object_key)
            if _finish_expired_media_cleanup(claim=claim):
                deleted += 1
        except Exception:
            failed += 1
            logger.warning(
                "Could not clean up an expired private media upload.",
                exc_info=True,
            )

    return ExpiredMediaCleanupResult(
        scanned=len(expired_upload_ids),
        deleted=deleted,
        failed=failed,
    )


__all__ = (
    "ExpiredMediaCleanupResult",
    "cleanup_expired_media_uploads",
    "expired_media_upload_count",
)
