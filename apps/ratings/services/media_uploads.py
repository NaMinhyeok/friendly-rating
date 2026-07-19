from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..media_storage import (
    PRIVATE_MEDIA_CACHE_CONTROL,
    MediaObjectNotFoundError,
    MediaStorageError,
    MediaStorageGateway,
    StoredMediaObject,
    get_media_storage_gateway,
)
from ..models import MediaAttachment, Participant, ScoreChange, ScoreChangeComment

MEBIBYTE = 1024 * 1024
MAX_IMAGE_SIZE = 10 * MEBIBYTE
MAX_VIDEO_SIZE = 100 * MEBIBYTE
MAX_SCORE_CHANGE_ATTACHMENTS = 1
MAX_COMMENT_IMAGE_ATTACHMENTS = 4
MAX_COMMENT_VIDEO_ATTACHMENTS = 1
MAX_OUTSTANDING_UPLOADS_PER_PARTICIPANT = 20
MAX_OUTSTANDING_UPLOAD_BYTES_PER_PARTICIPANT = 512 * MEBIBYTE
FINALIZATION_LEASE_SECONDS = 120
READY_UPLOAD_TTL_SECONDS = 24 * 60 * 60

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
ALLOWED_VIDEO_CONTENT_TYPES = frozenset({"video/mp4", "video/webm", "video/quicktime"})
_MP4_BRANDS = frozenset(
    {
        b"M4A ",
        b"M4V ",
        b"avc1",
        b"dash",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"iso7",
        b"iso8",
        b"iso9",
        b"isom",
        b"mp41",
        b"mp42",
    }
)


class MediaUploadError(Exception):
    """Base class for expected media-upload workflow failures."""


class MediaUploadValidationError(MediaUploadError):
    """Raised when requested or uploaded media violates the media policy."""


class MediaUploadNotFoundError(MediaUploadError):
    """Raised when a media upload ID does not exist."""


class MediaUploadPermissionError(MediaUploadError):
    """Raised when a participant cannot use or read a media upload."""


class MediaUploadStateError(MediaUploadError):
    """Raised when a media upload is expired or in the wrong lifecycle state."""


class MediaUploadStorageError(MediaUploadError):
    """Raised when private object storage is unavailable."""


@dataclass(frozen=True, slots=True)
class InitiatedMediaUpload:
    upload_id: UUID
    upload_url: str
    required_headers: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedMediaUpload:
    attachment: MediaAttachment


@dataclass(frozen=True, slots=True)
class _CompletionClaim:
    attachment: MediaAttachment
    needs_finalization: bool
    token: UUID | None
    reclaim_token: UUID | None


@dataclass(frozen=True, slots=True)
class _DiscardClaim:
    upload_id: UUID
    uploader_id: int
    finalization_token: UUID | None
    object_keys: tuple[str, ...]


def _positive_setting(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MediaUploadStorageError(f"{name} must be a positive integer.")
    return value


def _normalize_content_type(content_type: str) -> str:
    normalized = content_type.partition(";")[0].strip().lower()
    if not normalized or len(normalized) > 100:
        raise MediaUploadValidationError("지원하지 않는 파일 형식이에요.")
    return normalized


def _normalize_original_name(original_name: str) -> str:
    normalized = original_name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if (
        not normalized
        or len(normalized) > 255
        or any(ord(character) < 32 for character in normalized)
    ):
        raise MediaUploadValidationError("올바른 파일 이름을 입력해 주세요.")
    return normalized


def _coerce_purpose(
    purpose: MediaAttachment.Purpose | str,
) -> MediaAttachment.Purpose:
    try:
        return MediaAttachment.Purpose(purpose)
    except ValueError as error:
        raise MediaUploadValidationError(
            "올바른 업로드 용도를 선택해 주세요."
        ) from error


def _coerce_kind(kind: MediaAttachment.Kind | str) -> MediaAttachment.Kind:
    try:
        return MediaAttachment.Kind(kind)
    except ValueError as error:
        raise MediaUploadValidationError(
            "올바른 미디어 종류를 선택해 주세요."
        ) from error


def _validate_requested_media(
    *,
    purpose: MediaAttachment.Purpose,
    kind: MediaAttachment.Kind,
    content_type: str,
    expected_size: int,
) -> None:
    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
        raise MediaUploadValidationError("파일 크기가 올바르지 않아요.")
    if expected_size <= 0:
        raise MediaUploadValidationError("빈 파일은 올릴 수 없어요.")

    if kind == MediaAttachment.Kind.IMAGE:
        if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            raise MediaUploadValidationError("지원하지 않는 이미지 형식이에요.")
        if expected_size > MAX_IMAGE_SIZE:
            raise MediaUploadValidationError("이미지는 10MB 이하로 올려 주세요.")
        return

    if purpose == MediaAttachment.Purpose.SCORE_CHANGE:
        raise MediaUploadValidationError("점수 변경에는 이미지만 올릴 수 있어요.")
    if content_type not in ALLOWED_VIDEO_CONTENT_TYPES:
        raise MediaUploadValidationError("지원하지 않는 영상 형식이에요.")
    if expected_size > MAX_VIDEO_SIZE:
        raise MediaUploadValidationError("영상은 100MB 이하로 올려 주세요.")


@transaction.atomic
def _create_pending_attachment(
    *,
    upload_id: UUID,
    uploader: Participant,
    score_change: ScoreChange | None,
    purpose: MediaAttachment.Purpose,
    kind: MediaAttachment.Kind,
    object_key: str,
    original_name: str,
    content_type: str,
    expected_size: int,
    expires_at: datetime,
) -> MediaAttachment:
    if uploader.pk is None:
        raise MediaUploadPermissionError("로그인한 참가자만 파일을 올릴 수 있어요.")

    # Every upload initiation for one participant locks the same row. This keeps
    # concurrent requests from all observing quota headroom and oversubscribing
    # private storage together on PostgreSQL.
    Participant.objects.select_for_update().get(pk=uploader.pk)
    outstanding = list(
        MediaAttachment.objects.filter(
            uploader_id=uploader.pk,
            status__in=(
                MediaAttachment.Status.PENDING,
                MediaAttachment.Status.FINALIZING,
                MediaAttachment.Status.RECLAIMING,
                MediaAttachment.Status.READY,
                MediaAttachment.Status.DELETING,
            ),
        )
        .only("expected_size")
        .order_by("created_at", "pk")[:MAX_OUTSTANDING_UPLOADS_PER_PARTICIPANT]
    )
    if len(outstanding) >= MAX_OUTSTANDING_UPLOADS_PER_PARTICIPANT or (
        sum(attachment.expected_size for attachment in outstanding) + expected_size
        > MAX_OUTSTANDING_UPLOAD_BYTES_PER_PARTICIPANT
    ):
        raise MediaUploadStateError(
            "완료되지 않은 업로드가 너무 많아요. 잠시 후 다시 시도해 주세요."
        )

    return MediaAttachment.objects.create(
        id=upload_id,
        uploader=uploader,
        score_change=(
            score_change if purpose == MediaAttachment.Purpose.COMMENT else None
        ),
        purpose=purpose,
        kind=kind,
        status=MediaAttachment.Status.PENDING,
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
        expected_size=expected_size,
        expires_at=expires_at,
    )


def _participant_can_access_score_change(
    *,
    participant: Participant,
    score_change: ScoreChange,
) -> bool:
    participant_id = participant.pk
    if participant_id is None:
        return False
    relationship_score = score_change.relationship_score
    return participant_id in {
        relationship_score.source_participant_id,
        relationship_score.target_participant_id,
    }


def _validate_initiation_parent(
    *,
    uploader: Participant,
    purpose: MediaAttachment.Purpose,
    score_change: ScoreChange | None,
) -> None:
    if uploader.pk is None:
        raise MediaUploadPermissionError("로그인한 참가자만 파일을 올릴 수 있어요.")
    if purpose == MediaAttachment.Purpose.SCORE_CHANGE:
        if score_change is not None:
            raise MediaUploadValidationError(
                "점수 변경 미디어는 점수 변경을 저장할 때 연결해 주세요."
            )
        return

    if score_change is None or score_change.pk is None:
        raise MediaUploadValidationError("댓글을 남길 점수 변경을 선택해 주세요.")
    if not _participant_can_access_score_change(
        participant=uploader,
        score_change=score_change,
    ):
        raise MediaUploadPermissionError("이 점수 변경에는 파일을 올릴 수 없어요.")


def detect_media_content_type(initial_bytes: bytes) -> str | None:
    if initial_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if initial_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(initial_bytes) >= 12
        and initial_bytes[:4] == b"RIFF"
        and initial_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    if initial_bytes.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if len(initial_bytes) >= 12 and initial_bytes[4:8] == b"ftyp":
        brands = {
            initial_bytes[index : index + 4]
            for index in range(8, min(len(initial_bytes), 64), 4)
        }
        if b"qt  " in brands:
            return "video/quicktime"
        if brands & _MP4_BRANDS:
            return "video/mp4"
    return None


def content_type_matches_signature(
    *,
    content_type: str,
    initial_bytes: bytes,
) -> bool:
    return detect_media_content_type(initial_bytes) == content_type


def initiate_media_upload(
    *,
    uploader: Participant,
    purpose: MediaAttachment.Purpose | str,
    kind: MediaAttachment.Kind | str,
    original_name: str,
    content_type: str,
    expected_size: int,
    score_change: ScoreChange | None = None,
    storage: MediaStorageGateway | None = None,
) -> InitiatedMediaUpload:
    normalized_purpose = _coerce_purpose(purpose)
    normalized_kind = _coerce_kind(kind)
    normalized_content_type = _normalize_content_type(content_type)
    normalized_original_name = _normalize_original_name(original_name)
    _validate_requested_media(
        purpose=normalized_purpose,
        kind=normalized_kind,
        content_type=normalized_content_type,
        expected_size=expected_size,
    )
    _validate_initiation_parent(
        uploader=uploader,
        purpose=normalized_purpose,
        score_change=score_change,
    )

    try:
        storage_gateway = storage or get_media_storage_gateway()
    except Exception as error:
        raise MediaUploadStorageError("업로드를 준비하지 못했어요.") from error

    # Activity provides a bounded cleanup backstop in addition to the scheduled
    # command. At most one expired upload for this participant is touched, so a
    # full quota can recover without putting an unbounded R2 loop on a web worker.
    from .media_cleanup import cleanup_expired_media_uploads

    if uploader.pk is None:
        raise MediaUploadPermissionError("로그인한 참가자만 파일을 올릴 수 있어요.")
    cleanup_expired_media_uploads(
        limit=1,
        storage=storage_gateway,
        uploader_id=uploader.pk,
    )

    expires_in = _positive_setting("MEDIA_UPLOAD_URL_TTL_SECONDS", 900)
    expires_at = timezone.now() + timedelta(seconds=expires_in)
    upload_id = uuid4()
    pending_key = f"pending/{upload_id}"
    attachment = _create_pending_attachment(
        upload_id=upload_id,
        uploader=uploader,
        score_change=score_change,
        purpose=normalized_purpose,
        kind=normalized_kind,
        object_key=pending_key,
        original_name=normalized_original_name,
        content_type=normalized_content_type,
        expected_size=expected_size,
        expires_at=expires_at,
    )

    try:
        upload_url = storage_gateway.generate_upload_url(
            object_key=pending_key,
            content_type=normalized_content_type,
            content_length=expected_size,
            expires_in=expires_in,
        )
    except Exception as error:
        MediaAttachment.objects.filter(
            pk=attachment.pk,
            status=MediaAttachment.Status.PENDING,
        ).delete()
        raise MediaUploadStorageError("업로드를 준비하지 못했어요.") from error

    return InitiatedMediaUpload(
        upload_id=upload_id,
        upload_url=upload_url,
        required_headers={
            "Content-Type": normalized_content_type,
            "Cache-Control": PRIVATE_MEDIA_CACHE_CONTROL,
        },
        expires_at=expires_at,
    )


@transaction.atomic(durable=True)
def _claim_media_upload(
    *,
    upload_id: UUID,
    uploader: Participant,
) -> _CompletionClaim:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(pk=upload_id)
    except MediaAttachment.DoesNotExist as error:
        raise MediaUploadNotFoundError("업로드를 찾을 수 없어요.") from error

    if attachment.uploader_id != uploader.pk:
        raise MediaUploadPermissionError("이 업로드를 완료할 수 없어요.")
    now = timezone.now()
    if attachment.status == MediaAttachment.Status.READY:
        if attachment.expires_at <= now:
            raise MediaUploadStateError(
                "첨부 준비 시간이 만료되었어요. 다시 선택해 주세요."
            )
        return _CompletionClaim(
            attachment=attachment,
            needs_finalization=False,
            token=None,
            reclaim_token=None,
        )
    if attachment.status == MediaAttachment.Status.ATTACHED:
        raise MediaUploadStateError("이미 사용한 업로드예요.")
    if attachment.status == MediaAttachment.Status.DELETING:
        raise MediaUploadStateError("만료된 업로드를 정리하고 있어요.")
    if attachment.status == MediaAttachment.Status.RECLAIMING:
        reclaim_token = attachment.finalization_token
        if reclaim_token is None:
            raise MediaUploadStateError("업로드 확인 상태가 올바르지 않아요.")
        return _CompletionClaim(
            attachment=attachment,
            needs_finalization=True,
            token=None,
            reclaim_token=reclaim_token,
        )
    if (
        attachment.status == MediaAttachment.Status.FINALIZING
        and attachment.expires_at > now
    ):
        raise MediaUploadStateError(
            "파일을 확인하고 있어요. 잠시 후 다시 시도해 주세요."
        )
    if attachment.status == MediaAttachment.Status.FINALIZING:
        reclaim_token = attachment.finalization_token
        if reclaim_token is None:
            raise MediaUploadStateError("업로드 확인 상태가 올바르지 않아요.")
        # Commit an invalidating tombstone before deleting the old immutable
        # object. The old worker can no longer mark its token READY, and a
        # failed delete leaves the token discoverable for an idempotent retry.
        attachment.status = MediaAttachment.Status.RECLAIMING
        attachment.save(update_fields=("status",))
        return _CompletionClaim(
            attachment=attachment,
            needs_finalization=True,
            token=None,
            reclaim_token=reclaim_token,
        )
    if attachment.status != MediaAttachment.Status.PENDING:
        raise MediaUploadStateError("업로드 상태가 변경되어 완료할 수 없어요.")
    if (
        attachment.status == MediaAttachment.Status.PENDING
        and attachment.expires_at <= now
    ):
        raise MediaUploadStateError("업로드 시간이 만료되었어요. 다시 선택해 주세요.")

    claim_token = uuid4()
    attachment.status = MediaAttachment.Status.FINALIZING
    attachment.finalization_token = claim_token
    attachment.expires_at = now + timedelta(seconds=FINALIZATION_LEASE_SECONDS)
    attachment.save(update_fields=("status", "finalization_token", "expires_at"))
    return _CompletionClaim(
        attachment=attachment,
        needs_finalization=True,
        token=claim_token,
        reclaim_token=None,
    )


@transaction.atomic(durable=True)
def _start_reclaimed_claim(
    *,
    upload_id: UUID,
    uploader: Participant,
    reclaim_token: UUID,
) -> _CompletionClaim:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(pk=upload_id)
    except MediaAttachment.DoesNotExist as error:
        raise MediaUploadNotFoundError("업로드를 찾을 수 없어요.") from error
    if attachment.uploader_id != uploader.pk:
        raise MediaUploadPermissionError("이 업로드를 완료할 수 없어요.")
    if (
        attachment.status != MediaAttachment.Status.RECLAIMING
        or attachment.finalization_token != reclaim_token
    ):
        raise MediaUploadStateError("업로드 상태가 변경되어 완료할 수 없어요.")

    claim_token = uuid4()
    attachment.status = MediaAttachment.Status.FINALIZING
    attachment.finalization_token = claim_token
    attachment.expires_at = timezone.now() + timedelta(
        seconds=FINALIZATION_LEASE_SECONDS
    )
    attachment.save(update_fields=("status", "finalization_token", "expires_at"))
    return _CompletionClaim(
        attachment=attachment,
        needs_finalization=True,
        token=claim_token,
        reclaim_token=None,
    )


@transaction.atomic(durable=True)
def _acknowledge_failed_finalization_cleanup(
    *,
    upload_id: UUID,
    claim_token: UUID,
) -> None:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(
            pk=upload_id,
            finalization_token=claim_token,
        )
    except MediaAttachment.DoesNotExist:
        return

    if attachment.status == MediaAttachment.Status.FINALIZING:
        attachment.status = MediaAttachment.Status.PENDING
        attachment.finalization_token = None
        attachment.save(update_fields=("status", "finalization_token"))
        return
    if attachment.status == MediaAttachment.Status.DELETING:
        # A discard keeps this token as a durable coordination tombstone until
        # its finalizer proves that the immutable claim object is gone.
        attachment.delete()


def _validate_stored_object(
    *,
    attachment: MediaAttachment,
    stored_object: StoredMediaObject,
) -> None:
    if stored_object.size != attachment.expected_size:
        raise MediaUploadValidationError("업로드된 파일 크기가 요청한 크기와 달라요.")
    if stored_object.content_type != attachment.content_type:
        raise MediaUploadValidationError("업로드된 파일 형식이 요청한 형식과 달라요.")
    if not content_type_matches_signature(
        content_type=attachment.content_type,
        initial_bytes=stored_object.initial_bytes,
    ):
        raise MediaUploadValidationError(
            "파일 내용이 선택한 이미지 또는 영상 형식과 일치하지 않아요."
        )


def _delete_object_best_effort(
    *,
    storage: MediaStorageGateway,
    object_key: str,
) -> bool:
    try:
        storage.delete_object(object_key=object_key)
    except Exception:
        logger.warning(
            "Could not clean up a private media staging object.",
            exc_info=True,
        )
        return False
    return True


def _delete_final_object_if_unreferenced(
    *,
    storage: MediaStorageGateway,
    upload_id: UUID,
    object_key: str,
) -> bool:
    if MediaAttachment.objects.filter(
        pk=upload_id,
        object_key=object_key,
        status__in=(MediaAttachment.Status.READY, MediaAttachment.Status.ATTACHED),
    ).exists():
        return True
    return _delete_object_best_effort(storage=storage, object_key=object_key)


def _clean_up_failed_finalization(
    *,
    storage: MediaStorageGateway,
    upload_id: UUID,
    claim_token: UUID,
    final_key: str,
    can_acknowledge_claim: bool,
) -> None:
    object_is_clean = _delete_final_object_if_unreferenced(
        storage=storage,
        upload_id=upload_id,
        object_key=final_key,
    )
    if object_is_clean and can_acknowledge_claim:
        _acknowledge_failed_finalization_cleanup(
            upload_id=upload_id,
            claim_token=claim_token,
        )


@transaction.atomic(durable=True)
def _mark_media_upload_ready(
    *,
    upload_id: UUID,
    uploader: Participant,
    claim_token: UUID,
    final_key: str,
    stored_object: StoredMediaObject,
) -> MediaAttachment:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(pk=upload_id)
    except MediaAttachment.DoesNotExist as error:
        raise MediaUploadNotFoundError("업로드를 찾을 수 없어요.") from error
    if attachment.uploader_id != uploader.pk:
        raise MediaUploadPermissionError("이 업로드를 완료할 수 없어요.")
    if (
        attachment.status != MediaAttachment.Status.FINALIZING
        or attachment.finalization_token != claim_token
    ):
        raise MediaUploadStateError("업로드 상태가 변경되어 완료할 수 없어요.")

    attachment.status = MediaAttachment.Status.READY
    attachment.object_key = final_key
    attachment.actual_size = stored_object.size
    attachment.etag = stored_object.etag
    attachment.finalized_at = timezone.now()
    attachment.expires_at = attachment.finalized_at + timedelta(
        seconds=READY_UPLOAD_TTL_SECONDS
    )
    attachment.finalization_token = None
    attachment.save(
        update_fields=(
            "status",
            "object_key",
            "actual_size",
            "etag",
            "finalized_at",
            "expires_at",
            "finalization_token",
        )
    )
    return attachment


def complete_media_upload(
    *,
    upload_id: UUID,
    uploader: Participant,
    storage: MediaStorageGateway | None = None,
) -> CompletedMediaUpload:
    try:
        storage_gateway = storage or get_media_storage_gateway()
    except Exception as error:
        raise MediaUploadStorageError("파일 저장소에 연결하지 못했어요.") from error

    claim = _claim_media_upload(
        upload_id=upload_id,
        uploader=uploader,
    )
    if not claim.needs_finalization:
        return CompletedMediaUpload(attachment=claim.attachment)

    reclaim_token = claim.reclaim_token
    if reclaim_token is not None:
        try:
            storage_gateway.delete_object(
                object_key=f"media/{upload_id}/{reclaim_token}"
            )
        except Exception as error:
            # RECLAIMING and the old token were committed before this I/O, so
            # another completion or scheduled cleanup can safely retry it.
            raise MediaUploadStorageError(
                "이전 업로드 확인 작업을 정리하지 못했어요."
            ) from error
        claim = _start_reclaimed_claim(
            upload_id=upload_id,
            uploader=uploader,
            reclaim_token=reclaim_token,
        )

    attachment = claim.attachment
    claim_token = claim.token
    if claim_token is None:
        raise RuntimeError("A finalizing media upload has no claim token.")
    pending_key = attachment.object_key
    final_key = f"media/{attachment.pk}/{claim_token}"
    promotion_may_still_finish = False
    try:
        pending_object = storage_gateway.inspect_object(
            object_key=pending_key,
        )
        _validate_stored_object(
            attachment=attachment,
            stored_object=pending_object,
        )
        # A raised copy call is outcome-ambiguous: R2 may still finish it, so
        # its token cannot be acknowledged until the lease cleanup retries.
        promotion_may_still_finish = True
        storage_gateway.promote_object(
            source_key=pending_key,
            destination_key=final_key,
            content_type=attachment.content_type,
            original_name=attachment.original_name,
        )
        promotion_may_still_finish = False
        # A presigned PUT remains reusable until expiry. Validate the immutable
        # destination again after the copy so a concurrent overwrite of the
        # pending key cannot turn unverified bytes into accepted media.
        stored_object = storage_gateway.inspect_object(object_key=final_key)
        _validate_stored_object(
            attachment=attachment,
            stored_object=stored_object,
        )
    except MediaObjectNotFoundError as error:
        _clean_up_failed_finalization(
            storage=storage_gateway,
            upload_id=upload_id,
            claim_token=claim_token,
            final_key=final_key,
            can_acknowledge_claim=not promotion_may_still_finish,
        )
        raise MediaUploadStateError("업로드된 파일을 찾을 수 없어요.") from error
    except MediaStorageError as error:
        _clean_up_failed_finalization(
            storage=storage_gateway,
            upload_id=upload_id,
            claim_token=claim_token,
            final_key=final_key,
            can_acknowledge_claim=not promotion_may_still_finish,
        )
        raise MediaUploadStorageError("업로드된 파일을 확인하지 못했어요.") from error
    except MediaUploadError:
        _clean_up_failed_finalization(
            storage=storage_gateway,
            upload_id=upload_id,
            claim_token=claim_token,
            final_key=final_key,
            can_acknowledge_claim=not promotion_may_still_finish,
        )
        raise
    except Exception as error:
        _clean_up_failed_finalization(
            storage=storage_gateway,
            upload_id=upload_id,
            claim_token=claim_token,
            final_key=final_key,
            can_acknowledge_claim=not promotion_may_still_finish,
        )
        raise MediaUploadStorageError("업로드된 파일을 확인하지 못했어요.") from error

    try:
        ready_attachment = _mark_media_upload_ready(
            upload_id=upload_id,
            uploader=uploader,
            claim_token=claim_token,
            final_key=final_key,
            stored_object=stored_object,
        )
    except Exception:
        _clean_up_failed_finalization(
            storage=storage_gateway,
            upload_id=upload_id,
            claim_token=claim_token,
            final_key=final_key,
            can_acknowledge_claim=True,
        )
        raise

    # The accepted object now lives at an immutable key. Deleting the reusable
    # pending key is best-effort because the bucket lifecycle is the backstop.
    _delete_object_best_effort(storage=storage_gateway, object_key=pending_key)
    return CompletedMediaUpload(attachment=ready_attachment)


@transaction.atomic(durable=True)
def _claim_media_upload_discard(
    *,
    upload_id: UUID,
    uploader: Participant,
) -> _DiscardClaim | None:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(pk=upload_id)
    except MediaAttachment.DoesNotExist:
        return None

    if uploader.pk is None or attachment.uploader_id != uploader.pk:
        raise MediaUploadPermissionError("이 업로드를 폐기할 수 없어요.")
    if attachment.status == MediaAttachment.Status.ATTACHED:
        raise MediaUploadStateError("이미 사용한 업로드예요.")
    if attachment.status not in {
        MediaAttachment.Status.PENDING,
        MediaAttachment.Status.FINALIZING,
        MediaAttachment.Status.RECLAIMING,
        MediaAttachment.Status.READY,
        MediaAttachment.Status.DELETING,
    }:
        raise MediaUploadStateError("업로드 상태가 변경되어 폐기할 수 없어요.")

    # Commit this tombstone before external I/O. A concurrent finalizer can no
    # longer mark its claim READY, while a failed object delete remains
    # discoverable through the row for an idempotent retry.
    discard_started_at = timezone.now()
    finalization_token = attachment.finalization_token
    discard_expires_at = discard_started_at
    if finalization_token is not None:
        discard_expires_at = discard_started_at + timedelta(
            seconds=FINALIZATION_LEASE_SECONDS
        )
    attachment.status = MediaAttachment.Status.DELETING
    attachment.expires_at = discard_expires_at
    attachment.save(update_fields=("status", "expires_at"))

    object_keys = [f"pending/{attachment.pk}", attachment.object_key]
    if attachment.finalization_token is not None:
        object_keys.append(f"media/{attachment.pk}/{attachment.finalization_token}")
    return _DiscardClaim(
        upload_id=attachment.pk,
        uploader_id=attachment.uploader_id,
        finalization_token=finalization_token,
        object_keys=tuple(dict.fromkeys(object_keys)),
    )


@transaction.atomic(durable=True)
def _finish_media_upload_discard(*, claim: _DiscardClaim) -> None:
    try:
        attachment = MediaAttachment.objects.select_for_update().get(
            pk=claim.upload_id,
            uploader_id=claim.uploader_id,
            status=MediaAttachment.Status.DELETING,
            finalization_token__isnull=True,
        )
    except MediaAttachment.DoesNotExist:
        # Another retry may already have removed the same tombstone.
        return
    attachment.delete()


def discard_media_upload(
    *,
    upload_id: UUID,
    uploader: Participant,
    storage: MediaStorageGateway | None = None,
) -> None:
    claim = _claim_media_upload_discard(
        upload_id=upload_id,
        uploader=uploader,
    )
    if claim is None:
        return

    try:
        storage_gateway = storage or get_media_storage_gateway()
    except Exception as error:
        raise MediaUploadStorageError("파일 저장소에 연결하지 못했어요.") from error
    deletion_failed = False
    for object_key in claim.object_keys:
        try:
            storage_gateway.delete_object(object_key=object_key)
        except Exception:
            deletion_failed = True
            logger.warning(
                "Could not discard a private media upload object.",
                exc_info=True,
            )
    if deletion_failed:
        raise MediaUploadStorageError("업로드된 파일을 정리하지 못했어요.")

    if claim.finalization_token is None:
        _finish_media_upload_discard(claim=claim)


def _normalize_upload_ids(upload_ids: Sequence[UUID]) -> tuple[UUID, ...]:
    normalized_ids = tuple(upload_ids)
    if any(not isinstance(upload_id, UUID) for upload_id in normalized_ids):
        raise MediaUploadValidationError("올바른 업로드 ID를 입력해 주세요.")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise MediaUploadValidationError("같은 파일을 두 번 연결할 수 없어요.")
    return normalized_ids


def _validate_attachment_group(
    *,
    attachments: tuple[MediaAttachment, ...],
    uploader: Participant,
    purpose: MediaAttachment.Purpose,
    score_change: ScoreChange,
    comment: ScoreChangeComment | None,
) -> None:
    now = timezone.now()
    for attachment in attachments:
        if attachment.uploader_id != uploader.pk:
            raise MediaUploadPermissionError("다른 참가자의 파일은 사용할 수 없어요.")
        if attachment.status != MediaAttachment.Status.READY:
            raise MediaUploadStateError("사용할 준비가 되지 않은 업로드가 있어요.")
        if attachment.expires_at <= now:
            raise MediaUploadStateError(
                "첨부 준비 시간이 만료된 업로드가 있어요. 다시 선택해 주세요."
            )
        if attachment.purpose != purpose:
            raise MediaUploadValidationError("업로드 용도가 올바르지 않아요.")

    if purpose == MediaAttachment.Purpose.SCORE_CHANGE:
        if comment is not None:
            raise MediaUploadValidationError("점수 변경 미디어에 댓글이 지정되었어요.")
        if len(attachments) > MAX_SCORE_CHANGE_ATTACHMENTS:
            raise MediaUploadValidationError(
                "점수 변경에는 이미지 한 장만 올릴 수 있어요."
            )
        if any(
            attachment.kind != MediaAttachment.Kind.IMAGE
            or attachment.score_change_id is not None
            for attachment in attachments
        ):
            raise MediaUploadValidationError(
                "점수 변경에는 아직 사용하지 않은 이미지만 올릴 수 있어요."
            )
        return

    if comment is None or comment.score_change_id != score_change.pk:
        raise MediaUploadValidationError("댓글과 점수 변경이 일치하지 않아요.")
    if any(
        attachment.score_change_id != score_change.pk
        or attachment.comment_id is not None
        for attachment in attachments
    ):
        raise MediaUploadValidationError(
            "다른 점수 변경에 준비한 파일은 사용할 수 없어요."
        )

    kinds = {attachment.kind for attachment in attachments}
    if kinds == {MediaAttachment.Kind.IMAGE}:
        if len(attachments) > MAX_COMMENT_IMAGE_ATTACHMENTS:
            raise MediaUploadValidationError("이미지는 최대 4장까지 올릴 수 있어요.")
        return
    if kinds == {MediaAttachment.Kind.VIDEO}:
        if len(attachments) > MAX_COMMENT_VIDEO_ATTACHMENTS:
            raise MediaUploadValidationError("영상은 한 개만 올릴 수 있어요.")
        return
    raise MediaUploadValidationError("이미지와 영상을 함께 올릴 수 없어요.")


@transaction.atomic
def attach_media_uploads(
    *,
    upload_ids: Sequence[UUID],
    uploader: Participant,
    purpose: MediaAttachment.Purpose | str,
    score_change: ScoreChange,
    comment: ScoreChangeComment | None = None,
) -> tuple[MediaAttachment, ...]:
    normalized_ids = _normalize_upload_ids(upload_ids)
    normalized_purpose = _coerce_purpose(purpose)
    if not normalized_ids:
        return ()
    if score_change.pk is None:
        raise MediaUploadValidationError("저장되지 않은 점수 변경이에요.")

    locked_attachments = {
        attachment.pk: attachment
        for attachment in MediaAttachment.objects.select_for_update().filter(
            pk__in=normalized_ids
        )
    }
    if len(locked_attachments) != len(normalized_ids):
        raise MediaUploadNotFoundError("업로드를 찾을 수 없어요.")
    attachments = tuple(locked_attachments[upload_id] for upload_id in normalized_ids)
    _validate_attachment_group(
        attachments=attachments,
        uploader=uploader,
        purpose=normalized_purpose,
        score_change=score_change,
        comment=comment,
    )

    for position, attachment in enumerate(attachments):
        attachment.score_change = score_change
        attachment.comment = comment
        attachment.position = position
        attachment.status = MediaAttachment.Status.ATTACHED
        attachment.save(update_fields=("score_change", "comment", "position", "status"))
    if comment is not None and comment.media_count != len(attachments):
        comment.media_count = len(attachments)
        comment.save(update_fields=("media_count",))
    return attachments


def attach_score_change_media_uploads(
    *,
    upload_ids: Sequence[UUID],
    uploader: Participant,
    score_change: ScoreChange,
) -> tuple[MediaAttachment, ...]:
    return attach_media_uploads(
        upload_ids=upload_ids,
        uploader=uploader,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        score_change=score_change,
    )


def attach_comment_media_uploads(
    *,
    upload_ids: Sequence[UUID],
    uploader: Participant,
    score_change: ScoreChange,
    comment: ScoreChangeComment,
) -> tuple[MediaAttachment, ...]:
    return attach_media_uploads(
        upload_ids=upload_ids,
        uploader=uploader,
        purpose=MediaAttachment.Purpose.COMMENT,
        score_change=score_change,
        comment=comment,
    )


def generate_media_download_url(
    *,
    attachment: MediaAttachment,
    participant: Participant,
    storage: MediaStorageGateway | None = None,
) -> str:
    if attachment.status != MediaAttachment.Status.ATTACHED:
        raise MediaUploadNotFoundError("미디어를 찾을 수 없어요.")
    score_change = attachment.score_change
    if score_change is None or not _participant_can_access_score_change(
        participant=participant,
        score_change=score_change,
    ):
        raise MediaUploadPermissionError("이 미디어를 볼 수 없어요.")

    expires_in = _positive_setting("MEDIA_DOWNLOAD_URL_TTL_SECONDS", 300)
    try:
        storage_gateway = storage or get_media_storage_gateway()
        return storage_gateway.generate_download_url(
            object_key=attachment.object_key,
            content_type=attachment.content_type,
            original_name=attachment.original_name,
            expires_in=expires_in,
        )
    except Exception as error:
        raise MediaUploadStorageError("미디어를 불러오지 못했어요.") from error


# Compatibility names for adapters that describe the lifecycle as create/finalize.
create_media_upload = initiate_media_upload
finalize_media_upload = complete_media_upload


__all__ = (
    "ALLOWED_IMAGE_CONTENT_TYPES",
    "ALLOWED_VIDEO_CONTENT_TYPES",
    "CompletedMediaUpload",
    "InitiatedMediaUpload",
    "MAX_COMMENT_IMAGE_ATTACHMENTS",
    "MAX_COMMENT_VIDEO_ATTACHMENTS",
    "MAX_IMAGE_SIZE",
    "MAX_OUTSTANDING_UPLOAD_BYTES_PER_PARTICIPANT",
    "MAX_OUTSTANDING_UPLOADS_PER_PARTICIPANT",
    "MAX_SCORE_CHANGE_ATTACHMENTS",
    "MAX_VIDEO_SIZE",
    "READY_UPLOAD_TTL_SECONDS",
    "MediaUploadError",
    "MediaUploadNotFoundError",
    "MediaUploadPermissionError",
    "MediaUploadStateError",
    "MediaUploadStorageError",
    "MediaUploadValidationError",
    "attach_comment_media_uploads",
    "attach_media_uploads",
    "attach_score_change_media_uploads",
    "complete_media_upload",
    "content_type_matches_signature",
    "create_media_upload",
    "detect_media_content_type",
    "discard_media_upload",
    "finalize_media_upload",
    "generate_media_download_url",
    "initiate_media_upload",
)
