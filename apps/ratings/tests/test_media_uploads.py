from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from ..media_storage import MediaStorageGateway, StoredMediaObject
from ..models import MediaAttachment, ScoreChange, ScoreChangeComment
from ..services import (
    MediaUploadNotFoundError,
    MediaUploadPermissionError,
    MediaUploadStateError,
    MediaUploadStorageError,
    MediaUploadValidationError,
    add_score_change_comment,
    change_relationship_score,
    complete_media_upload,
    discard_media_upload,
    generate_media_download_url,
    initiate_media_upload,
)
from ..services.media_uploads import detect_media_content_type

pytestmark = pytest.mark.django_db

MEBIBYTE = 1024 * 1024


@dataclass
class FakeMediaStorage(MediaStorageGateway):
    stored_object: StoredMediaObject | None = None
    upload_requests: list[tuple[str, str, int, int]] = field(default_factory=list)
    promotion_requests: list[tuple[str, str, str, str]] = field(default_factory=list)
    deletion_requests: list[str] = field(default_factory=list)
    deletion_statuses: list[tuple[str, str]] = field(default_factory=list)
    inspection_requests: list[str] = field(default_factory=list)
    download_requests: list[tuple[str, str, str, int]] = field(default_factory=list)
    deletion_failures: set[str] = field(default_factory=set)
    observe_upload_id: UUID | None = None

    def generate_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        self.upload_requests.append(
            (object_key, content_type, content_length, expires_in)
        )
        return f"https://r2.invalid/upload/{object_key}"

    def inspect_object(self, *, object_key: str) -> StoredMediaObject:
        self.inspection_requests.append(object_key)
        if self.stored_object is None:
            raise AssertionError(f"No stored object configured for {object_key}")
        return self.stored_object

    def promote_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
        original_name: str,
    ) -> None:
        self.promotion_requests.append(
            (source_key, destination_key, content_type, original_name)
        )

    def delete_object(self, *, object_key: str) -> None:
        self.deletion_requests.append(object_key)
        if self.observe_upload_id is not None:
            status = MediaAttachment.objects.values_list("status", flat=True).get(
                pk=self.observe_upload_id
            )
            self.deletion_statuses.append((object_key, status))
        if object_key in self.deletion_failures:
            raise RuntimeError("simulated R2 delete failure")

    def generate_download_url(
        self,
        *,
        object_key: str,
        content_type: str,
        original_name: str,
        expires_in: int,
    ) -> str:
        self.download_requests.append(
            (object_key, content_type, original_name, expires_in)
        )
        return f"https://r2.invalid/download/{object_key}"


def _ready_attachment(
    *,
    uploader,
    purpose: str,
    kind: str = MediaAttachment.Kind.IMAGE,
    score_change: ScoreChange | None = None,
    suffix: str = "one",
) -> MediaAttachment:
    content_type = "image/jpeg" if kind == MediaAttachment.Kind.IMAGE else "video/mp4"
    byte_size = 512 if kind == MediaAttachment.Kind.IMAGE else 2048
    return MediaAttachment.objects.create(
        uploader=uploader,
        score_change=score_change,
        purpose=purpose,
        kind=kind,
        status=MediaAttachment.Status.READY,
        object_key=f"media/test-{suffix}",
        original_name=f"test-{suffix}.bin",
        content_type=content_type,
        expected_size=byte_size,
        actual_size=byte_size,
        etag=f"etag-{suffix}",
        expires_at=timezone.now() + timedelta(minutes=10),
        finalized_at=timezone.now(),
    )


def _claim_token_from_final_key(*, object_key: str, upload_id: UUID) -> UUID:
    prefix, key_upload_id, claim_token = object_key.split("/")
    assert prefix == "media"
    assert key_upload_id == str(upload_id)
    return UUID(claim_token)


def _pending_attachment(
    *,
    uploader,
    suffix: str,
    expected_size: int,
    purpose: str = MediaAttachment.Purpose.SCORE_CHANGE,
    kind: str = MediaAttachment.Kind.IMAGE,
    score_change: ScoreChange | None = None,
) -> MediaAttachment:
    content_type = "image/jpeg" if kind == MediaAttachment.Kind.IMAGE else "video/mp4"
    return MediaAttachment.objects.create(
        uploader=uploader,
        score_change=score_change,
        purpose=purpose,
        kind=kind,
        status=MediaAttachment.Status.PENDING,
        object_key=f"pending/quota-{suffix}",
        original_name=f"quota-{suffix}.bin",
        content_type=content_type,
        expected_size=expected_size,
        expires_at=timezone.now() + timedelta(minutes=10),
    )


@pytest.mark.parametrize(
    ("initial_bytes", "expected"),
    (
        (b"\xff\xd8\xffrest", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPrest", "image/webp"),
        (b"\x1aE\xdf\xa3rest", "video/webm"),
        (b"\x00\x00\x00\x18ftypisomrest", "video/mp4"),
        (b"\x00\x00\x00\x18ftypqt  rest", "video/quicktime"),
        (b"<script>alert(1)</script>", None),
    ),
)
def test_detects_media_from_file_signature(initial_bytes, expected):
    assert detect_media_content_type(initial_bytes) == expected


def test_initiates_private_score_change_image_upload(participant_pair, settings):
    settings.MEDIA_UPLOAD_URL_TTL_SECONDS = 120
    storage = FakeMediaStorage()

    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="../오늘.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.status == MediaAttachment.Status.PENDING
    assert attachment.score_change is None
    assert attachment.original_name == "오늘.jpg"
    assert attachment.object_key == f"pending/{attachment.pk}"
    assert initiated.required_headers == {
        "Content-Type": "image/jpeg",
        "Cache-Control": "private, no-store, max-age=0",
    }
    assert initiated.upload_url.endswith(attachment.object_key)
    assert storage.upload_requests == [(attachment.object_key, "image/jpeg", 512, 120)]


def test_score_change_upload_rejects_video_before_writing(participant_pair):
    with pytest.raises(
        MediaUploadValidationError,
        match="점수 변경에는 이미지만",
    ):
        initiate_media_upload(
            uploader=participant_pair.first,
            purpose=MediaAttachment.Purpose.SCORE_CHANGE,
            kind=MediaAttachment.Kind.VIDEO,
            original_name="clip.mp4",
            content_type="video/mp4",
            expected_size=1024,
            storage=FakeMediaStorage(),
        )

    assert not MediaAttachment.objects.exists()


def test_completion_validates_and_promotes_to_an_immutable_key(participant_pair):
    storage = FakeMediaStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="accepted-etag",
            initial_bytes=b"\xff\xd8\xffpayload",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    completed = complete_media_upload(
        upload_id=initiated.upload_id,
        uploader=participant_pair.first,
        storage=storage,
    )

    completed.attachment.refresh_from_db()
    attachment = completed.attachment
    assert attachment.status == MediaAttachment.Status.READY
    claim_token = _claim_token_from_final_key(
        object_key=attachment.object_key,
        upload_id=attachment.pk,
    )
    assert claim_token.version == 4
    assert attachment.finalization_token is None
    assert attachment.actual_size == 512
    assert attachment.etag == "accepted-etag"
    assert attachment.finalized_at is not None
    assert attachment.expires_at == attachment.finalized_at + timedelta(hours=24)
    assert storage.promotion_requests == [
        (
            f"pending/{attachment.pk}",
            attachment.object_key,
            "image/jpeg",
            "moment.jpg",
        )
    ]
    assert storage.inspection_requests == [
        f"pending/{attachment.pk}",
        attachment.object_key,
    ]
    assert storage.deletion_requests == [f"pending/{attachment.pk}"]

    repeated = complete_media_upload(
        upload_id=initiated.upload_id,
        uploader=participant_pair.first,
        storage=storage,
    )
    assert repeated.attachment.pk == attachment.pk
    assert len(storage.promotion_requests) == 1


@pytest.mark.parametrize(
    ("status", "has_claim_token", "is_ready"),
    (
        (MediaAttachment.Status.PENDING, False, False),
        (MediaAttachment.Status.FINALIZING, True, False),
        (MediaAttachment.Status.RECLAIMING, True, False),
        (MediaAttachment.Status.READY, False, True),
        (MediaAttachment.Status.DELETING, True, False),
    ),
)
def test_discard_tombstones_and_removes_every_unattached_upload_state(
    participant_pair,
    status,
    has_claim_token,
    is_ready,
):
    upload_id = uuid4()
    pending_key = f"pending/{upload_id}"
    claim_token = uuid4() if has_claim_token else None
    ready_key = f"media/{upload_id}/{uuid4()}"
    attachment = MediaAttachment.objects.create(
        id=upload_id,
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=status,
        object_key=ready_key if is_ready else pending_key,
        original_name="discard.jpg",
        content_type="image/jpeg",
        expected_size=512,
        actual_size=512 if is_ready else None,
        etag="ready-etag" if is_ready else "",
        expires_at=timezone.now() + timedelta(minutes=10),
        finalized_at=timezone.now() if is_ready else None,
        finalization_token=claim_token,
    )
    storage = FakeMediaStorage(observe_upload_id=attachment.pk)

    discard_media_upload(
        upload_id=attachment.pk,
        uploader=participant_pair.first,
        storage=storage,
    )

    assert not MediaAttachment.objects.filter(pk=attachment.pk).exists()
    expected_keys = [pending_key]
    if is_ready:
        expected_keys.append(ready_key)
    if claim_token is not None:
        expected_keys.append(f"media/{attachment.pk}/{claim_token}")
    assert storage.deletion_requests == expected_keys
    assert storage.deletion_statuses == [
        (object_key, MediaAttachment.Status.DELETING) for object_key in expected_keys
    ]


def test_discard_rejects_another_uploader_without_changing_state(participant_pair):
    attachment = _pending_attachment(
        uploader=participant_pair.first,
        suffix="discard-owner",
        expected_size=512,
    )
    storage = FakeMediaStorage()

    with pytest.raises(MediaUploadPermissionError, match="폐기할 수 없어요"):
        discard_media_upload(
            upload_id=attachment.pk,
            uploader=participant_pair.second,
            storage=storage,
        )

    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.PENDING
    assert storage.deletion_requests == []


def test_discard_refuses_an_attachment_already_used_by_a_score_change(
    participant_pair,
):
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="discard-attached",
    )
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=1,
        media_upload_ids=(attachment.pk,),
    )
    storage = FakeMediaStorage()

    with pytest.raises(MediaUploadStateError, match="이미 사용한"):
        discard_media_upload(
            upload_id=attachment.pk,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.ATTACHED
    assert attachment.score_change == change
    assert storage.deletion_requests == []


def test_discard_failure_keeps_a_retryable_tombstone_and_attempts_every_key(
    participant_pair,
):
    upload_id = uuid4()
    pending_key = f"pending/{upload_id}"
    claim_token = uuid4()
    final_key = f"media/{upload_id}/{claim_token}"
    attachment = MediaAttachment.objects.create(
        id=upload_id,
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.FINALIZING,
        object_key=pending_key,
        original_name="retry-discard.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
        finalization_token=claim_token,
    )
    storage = FakeMediaStorage(
        deletion_failures={pending_key},
        observe_upload_id=attachment.pk,
    )

    with pytest.raises(MediaUploadStorageError, match="정리하지 못했어요"):
        discard_media_upload(
            upload_id=attachment.pk,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
    assert attachment.expires_at <= timezone.now()
    assert attachment.finalization_token == claim_token
    assert storage.deletion_requests == [pending_key, final_key]
    assert storage.deletion_statuses == [
        (pending_key, MediaAttachment.Status.DELETING),
        (final_key, MediaAttachment.Status.DELETING),
    ]

    storage.deletion_failures.clear()
    discard_media_upload(
        upload_id=attachment.pk,
        uploader=participant_pair.first,
        storage=storage,
    )

    assert not MediaAttachment.objects.filter(pk=attachment.pk).exists()
    assert storage.deletion_requests == [
        pending_key,
        final_key,
        pending_key,
        final_key,
    ]


def test_discard_invalidates_a_completion_between_promotion_and_ready(
    participant_pair,
):
    storage = FakeMediaStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="accepted-etag",
            initial_bytes=b"\xff\xd8\xffpayload",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="discard-race.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    class DiscardDuringPromotionStorage(FakeMediaStorage):
        def promote_object(
            self,
            *,
            source_key: str,
            destination_key: str,
            content_type: str,
            original_name: str,
        ) -> None:
            super().promote_object(
                source_key=source_key,
                destination_key=destination_key,
                content_type=content_type,
                original_name=original_name,
            )
            discard_media_upload(
                upload_id=initiated.upload_id,
                uploader=participant_pair.first,
                storage=self,
            )

    racing_storage = DiscardDuringPromotionStorage(stored_object=storage.stored_object)

    with pytest.raises(MediaUploadNotFoundError, match="업로드를 찾을 수 없어요"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=racing_storage,
        )

    final_key = racing_storage.promotion_requests[0][1]
    assert not MediaAttachment.objects.filter(pk=initiated.upload_id).exists()
    assert racing_storage.deletion_requests == [
        f"pending/{initiated.upload_id}",
        final_key,
        final_key,
    ]


def test_completion_revalidates_the_copied_object_to_close_put_race(
    participant_pair,
):
    class ReplacedDuringCopyStorage(FakeMediaStorage):
        def inspect_object(self, *, object_key: str) -> StoredMediaObject:
            self.inspection_requests.append(object_key)
            if object_key.startswith("media/"):
                return StoredMediaObject(
                    size=512,
                    content_type="image/jpeg",
                    etag="replaced-etag",
                    initial_bytes=b"<html>replaced after validation</html>",
                )
            if self.stored_object is None:
                raise AssertionError(f"No stored object configured for {object_key}")
            return self.stored_object

    storage = ReplacedDuringCopyStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="initial-etag",
            initial_bytes=b"\xff\xd8\xffpayload",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    with pytest.raises(MediaUploadValidationError, match="파일 내용"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.status == MediaAttachment.Status.PENDING
    assert attachment.finalization_token is None
    failed_final_key = storage.promotion_requests[0][1]
    _claim_token_from_final_key(
        object_key=failed_final_key,
        upload_id=attachment.pk,
    )
    assert storage.deletion_requests == [failed_final_key]


def test_failed_final_object_cleanup_keeps_the_claim_discoverable(participant_pair):
    class InvalidCopyWithCleanupFailureStorage(FakeMediaStorage):
        def inspect_object(self, *, object_key: str) -> StoredMediaObject:
            self.inspection_requests.append(object_key)
            if object_key.startswith("media/"):
                return StoredMediaObject(
                    size=512,
                    content_type="image/jpeg",
                    etag="invalid-copy",
                    initial_bytes=b"<html>invalid copy</html>",
                )
            if self.stored_object is None:
                raise AssertionError(f"No stored object configured for {object_key}")
            return self.stored_object

        def delete_object(self, *, object_key: str) -> None:
            self.deletion_requests.append(object_key)
            if object_key.startswith("media/"):
                raise RuntimeError("simulated final object cleanup failure")

    storage = InvalidCopyWithCleanupFailureStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="pending-object",
            initial_bytes=b"\xff\xd8\xffpayload",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    with pytest.raises(MediaUploadValidationError, match="파일 내용"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    failed_final_key = storage.promotion_requests[0][1]
    assert attachment.status == MediaAttachment.Status.FINALIZING
    assert attachment.finalization_token == _claim_token_from_final_key(
        object_key=failed_final_key,
        upload_id=attachment.pk,
    )
    assert storage.deletion_requests == [failed_final_key]


def test_stale_finalization_lease_can_be_reclaimed(participant_pair):
    storage = FakeMediaStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="accepted-etag",
            initial_bytes=b"\xff\xd8\xffpayload",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )
    storage.observe_upload_id = initiated.upload_id
    stale_claim_token = uuid4()
    MediaAttachment.objects.filter(pk=initiated.upload_id).update(
        status=MediaAttachment.Status.FINALIZING,
        finalization_token=stale_claim_token,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    completed = complete_media_upload(
        upload_id=initiated.upload_id,
        uploader=participant_pair.first,
        storage=storage,
    )

    completed.attachment.refresh_from_db()
    assert completed.attachment.status == MediaAttachment.Status.READY
    reclaimed_claim_token = _claim_token_from_final_key(
        object_key=completed.attachment.object_key,
        upload_id=completed.attachment.pk,
    )
    assert reclaimed_claim_token != stale_claim_token
    assert completed.attachment.finalization_token is None
    assert storage.deletion_requests == [
        f"media/{completed.attachment.pk}/{stale_claim_token}",
        f"pending/{completed.attachment.pk}",
    ]
    assert storage.deletion_statuses[0] == (
        f"media/{completed.attachment.pk}/{stale_claim_token}",
        MediaAttachment.Status.RECLAIMING,
    )


def test_stale_claim_is_preserved_when_its_object_cannot_be_cleaned(
    participant_pair,
):
    storage = FakeMediaStorage()
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )
    storage.observe_upload_id = initiated.upload_id
    stale_claim_token = uuid4()
    stale_final_key = f"media/{initiated.upload_id}/{stale_claim_token}"
    MediaAttachment.objects.filter(pk=initiated.upload_id).update(
        status=MediaAttachment.Status.FINALIZING,
        finalization_token=stale_claim_token,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    storage.deletion_failures.add(stale_final_key)

    with pytest.raises(MediaUploadStorageError, match="이전 업로드"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.status == MediaAttachment.Status.RECLAIMING
    assert attachment.finalization_token == stale_claim_token
    assert storage.deletion_requests == [stale_final_key]
    assert storage.deletion_statuses == [
        (stale_final_key, MediaAttachment.Status.RECLAIMING)
    ]
    assert storage.promotion_requests == []

    storage.deletion_failures.clear()
    storage.stored_object = StoredMediaObject(
        size=512,
        content_type="image/jpeg",
        etag="accepted-after-retry",
        initial_bytes=b"\xff\xd8\xffpayload",
    )
    completed = complete_media_upload(
        upload_id=initiated.upload_id,
        uploader=participant_pair.first,
        storage=storage,
    )

    completed.attachment.refresh_from_db()
    assert completed.attachment.status == MediaAttachment.Status.READY
    assert completed.attachment.finalization_token is None
    assert storage.deletion_requests == [
        stale_final_key,
        stale_final_key,
        f"pending/{completed.attachment.pk}",
    ]


def test_stale_claim_is_not_deleted_when_reclaim_tombstone_write_fails(
    participant_pair,
    monkeypatch,
):
    storage = FakeMediaStorage()
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )
    stale_claim_token = uuid4()
    MediaAttachment.objects.filter(pk=initiated.upload_id).update(
        status=MediaAttachment.Status.FINALIZING,
        finalization_token=stale_claim_token,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    def fail_reclaiming_save(
        attachment: MediaAttachment,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        assert attachment.pk == initiated.upload_id
        assert attachment.status == MediaAttachment.Status.RECLAIMING
        raise RuntimeError("simulated tombstone write failure")

    monkeypatch.setattr(MediaAttachment, "save", fail_reclaiming_save)

    with pytest.raises(RuntimeError, match="tombstone write failure"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.status == MediaAttachment.Status.FINALIZING
    assert attachment.finalization_token == stale_claim_token
    assert storage.deletion_requests == []


def test_reclaimed_completion_cannot_delete_the_new_winners_object(participant_pair):
    valid_object = StoredMediaObject(
        size=512,
        content_type="image/jpeg",
        etag="accepted-etag",
        initial_bytes=b"\xff\xd8\xffpayload",
    )
    winner_storage = FakeMediaStorage(stored_object=valid_object)
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=winner_storage,
    )
    upload_id = initiated.upload_id

    class LosingCompletionStorage(FakeMediaStorage):
        def promote_object(
            self,
            *,
            source_key: str,
            destination_key: str,
            content_type: str,
            original_name: str,
        ) -> None:
            super().promote_object(
                source_key=source_key,
                destination_key=destination_key,
                content_type=content_type,
                original_name=original_name,
            )
            MediaAttachment.objects.filter(pk=upload_id).update(
                expires_at=timezone.now() - timedelta(seconds=1),
            )
            winner = complete_media_upload(
                upload_id=upload_id,
                uploader=participant_pair.first,
                storage=winner_storage,
            )
            self.winner_key = winner.attachment.object_key

    losing_storage = LosingCompletionStorage(stored_object=valid_object)

    with pytest.raises(MediaUploadStateError, match="상태가 변경"):
        complete_media_upload(
            upload_id=upload_id,
            uploader=participant_pair.first,
            storage=losing_storage,
        )

    attachment = MediaAttachment.objects.get(pk=upload_id)
    losing_key = losing_storage.promotion_requests[0][1]
    assert attachment.status == MediaAttachment.Status.READY
    assert attachment.object_key == losing_storage.winner_key
    assert attachment.object_key != losing_key
    assert losing_key in winner_storage.deletion_requests
    assert losing_key in losing_storage.deletion_requests
    assert attachment.object_key not in winner_storage.deletion_requests
    assert attachment.object_key not in losing_storage.deletion_requests


def test_active_finalization_lease_rejects_a_concurrent_completion(participant_pair):
    storage = FakeMediaStorage()
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )
    active_claim_token = uuid4()
    MediaAttachment.objects.filter(pk=initiated.upload_id).update(
        status=MediaAttachment.Status.FINALIZING,
        finalization_token=active_claim_token,
        expires_at=timezone.now() + timedelta(seconds=60),
    )

    with pytest.raises(MediaUploadStateError, match="확인하고 있어요"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.finalization_token == active_claim_token


@pytest.mark.parametrize(
    ("status", "finalization_token"),
    (
        (MediaAttachment.Status.PENDING, uuid4()),
        (MediaAttachment.Status.FINALIZING, None),
        (MediaAttachment.Status.RECLAIMING, None),
    ),
    ids=(
        "pending-with-token",
        "finalizing-without-token",
        "reclaiming-without-token",
    ),
)
def test_database_requires_claim_tokens_while_finalizing_or_reclaiming(
    participant_pair,
    status,
    finalization_token,
):
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="moment.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=FakeMediaStorage(),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaAttachment.objects.filter(pk=initiated.upload_id).update(
            status=status,
            finalization_token=finalization_token,
        )


def test_participant_cannot_exceed_twenty_outstanding_uploads(participant_pair):
    for index in range(20):
        _pending_attachment(
            uploader=participant_pair.first,
            suffix=f"count-{index}",
            expected_size=1,
        )
    storage = FakeMediaStorage()

    with pytest.raises(MediaUploadStateError, match="완료되지 않은 업로드"):
        initiate_media_upload(
            uploader=participant_pair.first,
            purpose=MediaAttachment.Purpose.SCORE_CHANGE,
            kind=MediaAttachment.Kind.IMAGE,
            original_name="one-too-many.jpg",
            content_type="image/jpeg",
            expected_size=1,
            storage=storage,
        )

    assert MediaAttachment.objects.filter(uploader=participant_pair.first).count() == 20
    assert storage.upload_requests == []

    other_participant_upload = initiate_media_upload(
        uploader=participant_pair.second,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="separate-quota.jpg",
        content_type="image/jpeg",
        expected_size=1,
        storage=storage,
    )
    assert other_participant_upload.upload_id is not None
    assert len(storage.upload_requests) == 1


def test_initiation_cleans_one_expired_upload_to_recover_quota(participant_pair):
    expired = tuple(
        _pending_attachment(
            uploader=participant_pair.first,
            suffix=f"expired-{index}",
            expected_size=1,
        )
        for index in range(20)
    )
    MediaAttachment.objects.filter(pk__in=[item.pk for item in expired]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    storage = FakeMediaStorage()

    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="replacement.jpg",
        content_type="image/jpeg",
        expected_size=1,
        storage=storage,
    )

    assert MediaAttachment.objects.filter(uploader=participant_pair.first).count() == 20
    assert storage.deletion_requests == [expired[0].object_key]
    assert storage.upload_requests[0][0] == f"pending/{initiated.upload_id}"


def test_participant_cannot_exceed_outstanding_upload_byte_quota(participant_pair):
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=1,
    )
    for index in range(5):
        _pending_attachment(
            uploader=participant_pair.first,
            suffix=f"bytes-{index}",
            expected_size=100 * MEBIBYTE,
            purpose=MediaAttachment.Purpose.COMMENT,
            kind=MediaAttachment.Kind.VIDEO,
            score_change=change,
        )
    storage = FakeMediaStorage()

    with pytest.raises(MediaUploadStateError, match="완료되지 않은 업로드"):
        initiate_media_upload(
            uploader=participant_pair.first,
            purpose=MediaAttachment.Purpose.COMMENT,
            kind=MediaAttachment.Kind.VIDEO,
            original_name="over-quota.mp4",
            content_type="video/mp4",
            expected_size=13 * MEBIBYTE,
            score_change=change,
            storage=storage,
        )

    assert MediaAttachment.objects.filter(uploader=participant_pair.first).count() == 5
    assert storage.upload_requests == []


def test_expired_ready_upload_cannot_be_attached(participant_pair):
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="expired-ready",
    )
    MediaAttachment.objects.filter(pk=attachment.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(MediaUploadStateError, match="첨부 준비 시간이 만료"):
        change_relationship_score(
            source_participant=participant_pair.first,
            delta=5,
            media_upload_ids=(attachment.pk,),
        )

    participant_pair.first_to_second.refresh_from_db()
    attachment.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()
    assert attachment.status == MediaAttachment.Status.READY


def test_expired_ready_upload_cannot_be_completed_again(participant_pair):
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="expired-completion",
    )
    MediaAttachment.objects.filter(pk=attachment.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    storage = FakeMediaStorage()

    with pytest.raises(MediaUploadStateError, match="첨부 준비 시간이 만료"):
        complete_media_upload(
            upload_id=attachment.pk,
            uploader=participant_pair.first,
            storage=storage,
        )

    assert storage.inspection_requests == []
    assert storage.promotion_requests == []


def test_comment_media_count_has_a_database_default_for_rolling_deploys(
    participant_pair,
):
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=1,
    )
    created_at = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO score_change_comment
                (score_change_id, author_id, content, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            [change.pk, participant_pair.second.pk, "구버전 댓글", created_at],
        )

    comment = ScoreChangeComment.objects.get(content="구버전 댓글")
    assert comment.media_count == 0


def test_completion_rejects_spoofed_content_and_restores_pending_state(
    participant_pair,
):
    storage = FakeMediaStorage(
        stored_object=StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="spoofed-etag",
            initial_bytes=b"<html>not an image</html>",
        )
    )
    initiated = initiate_media_upload(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        original_name="not-really.jpg",
        content_type="image/jpeg",
        expected_size=512,
        storage=storage,
    )

    with pytest.raises(MediaUploadValidationError, match="파일 내용"):
        complete_media_upload(
            upload_id=initiated.upload_id,
            uploader=participant_pair.first,
            storage=storage,
        )

    attachment = MediaAttachment.objects.get(pk=initiated.upload_id)
    assert attachment.status == MediaAttachment.Status.PENDING
    assert attachment.actual_size is None
    assert storage.promotion_requests == []


def test_score_change_atomically_consumes_the_upload(participant_pair):
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
    )

    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
        reason="사진과 함께",
        media_upload_ids=(attachment.pk,),
    )

    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.ATTACHED
    assert attachment.score_change == change
    assert attachment.comment is None
    assert attachment.position == 0


def test_invalid_score_upload_owner_rolls_back_score_and_history(participant_pair):
    attachment = _ready_attachment(
        uploader=participant_pair.second,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
    )

    with pytest.raises(MediaUploadPermissionError):
        change_relationship_score(
            source_participant=participant_pair.first,
            delta=5,
            media_upload_ids=(attachment.pk,),
        )

    participant_pair.first_to_second.refresh_from_db()
    attachment.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()
    assert attachment.status == MediaAttachment.Status.READY
    assert attachment.score_change is None


def test_media_only_comment_atomically_consumes_its_upload(participant_pair):
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
    )
    attachment = _ready_attachment(
        uploader=participant_pair.second,
        purpose=MediaAttachment.Purpose.COMMENT,
        score_change=change,
    )

    comment = add_score_change_comment(
        score_change=change,
        author=participant_pair.second,
        content="",
        media_upload_ids=(attachment.pk,),
    )

    attachment.refresh_from_db()
    assert comment.content == ""
    assert comment.media_count == 1
    assert attachment.status == MediaAttachment.Status.ATTACHED
    assert attachment.comment == comment
    assert ScoreChangeComment.objects.get() == comment


def test_comment_rejects_mixed_media_without_writing(participant_pair):
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
    )
    image = _ready_attachment(
        uploader=participant_pair.second,
        purpose=MediaAttachment.Purpose.COMMENT,
        score_change=change,
        suffix="image",
    )
    video = _ready_attachment(
        uploader=participant_pair.second,
        purpose=MediaAttachment.Purpose.COMMENT,
        kind=MediaAttachment.Kind.VIDEO,
        score_change=change,
        suffix="video",
    )

    with pytest.raises(MediaUploadValidationError, match="함께 올릴 수 없어요"):
        add_score_change_comment(
            score_change=change,
            author=participant_pair.second,
            content="섞이지 않아요",
            media_upload_ids=(image.pk, video.pk),
        )

    assert not ScoreChangeComment.objects.exists()
    assert set(MediaAttachment.objects.values_list("status", flat=True)) == {
        MediaAttachment.Status.READY
    }


def test_download_url_requires_thread_membership(participant_pair, settings):
    settings.MEDIA_DOWNLOAD_URL_TTL_SECONDS = 180
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
    )
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
    )
    attachment.score_change = change
    attachment.status = MediaAttachment.Status.ATTACHED
    attachment.save(update_fields=("score_change", "status"))
    storage = FakeMediaStorage()

    url = generate_media_download_url(
        attachment=attachment,
        participant=participant_pair.second,
        storage=storage,
    )

    assert url == f"https://r2.invalid/download/{attachment.object_key}"
    assert storage.download_requests == [
        (
            attachment.object_key,
            attachment.content_type,
            attachment.original_name,
            180,
        )
    ]

    with pytest.raises(MediaUploadPermissionError):
        generate_media_download_url(
            attachment=attachment,
            participant=type(participant_pair.first)(),
            storage=storage,
        )


def test_media_content_redirects_with_private_headers_for_a_thread_member(
    client,
    participant_pair,
    settings,
    monkeypatch,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
    )
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="content-view",
    )
    attachment.score_change = change
    attachment.status = MediaAttachment.Status.ATTACHED
    attachment.save(update_fields=("score_change", "status"))
    storage = FakeMediaStorage()
    monkeypatch.setattr(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        lambda: storage,
    )
    client.force_login(participant_pair.second.user)

    response = client.get(
        reverse("media-content", kwargs={"attachment_id": attachment.pk})
    )

    assert response.status_code == 302
    assert response.url == f"https://r2.invalid/download/{attachment.object_key}"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_media_content_hides_unattached_uploads(
    client,
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="unattached-content",
    )
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("media-content", kwargs={"attachment_id": attachment.pk})
    )

    assert response.status_code == 404


def test_media_content_returns_a_non_cacheable_503_when_r2_is_unavailable(
    client,
    participant_pair,
    settings,
    monkeypatch,
):
    class FailingDownloadStorage(FakeMediaStorage):
        def generate_download_url(
            self,
            *,
            object_key: str,
            content_type: str,
            original_name: str,
            expires_in: int,
        ) -> str:
            raise RuntimeError("simulated R2 outage")

    settings.MEDIA_UPLOADS_AVAILABLE = True
    change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=5,
    )
    attachment = _ready_attachment(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        suffix="unavailable-content",
    )
    attachment.score_change = change
    attachment.status = MediaAttachment.Status.ATTACHED
    attachment.save(update_fields=("score_change", "status"))
    monkeypatch.setattr(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        lambda: FailingDownloadStorage(),
    )
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("media-content", kwargs={"attachment_id": attachment.pk})
    )

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.content.decode() == "파일을 지금 불러올 수 없습니다."
