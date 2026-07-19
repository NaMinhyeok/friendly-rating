import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from uuid import UUID, uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..media_storage import (
    MediaStorageGateway,
    MediaStorageOperationError,
    StoredMediaObject,
)
from ..models import MediaAttachment, Participant, ScoreChange
from ..services.media_cleanup import (
    ExpiredMediaCleanupResult,
    cleanup_expired_media_uploads,
)
from ..services.media_uploads import (
    FINALIZATION_LEASE_SECONDS,
    discard_media_upload,
)

pytestmark = pytest.mark.django_db

_DML_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|REPLACE)\b",
    re.IGNORECASE,
)


@dataclass
class FakeMediaStorage(MediaStorageGateway):
    upload_requests: list[tuple[str, str, int, int]] = field(default_factory=list)
    inspection_requests: list[str] = field(default_factory=list)
    promotion_requests: list[tuple[str, str]] = field(default_factory=list)
    deletion_requests: list[str] = field(default_factory=list)
    deletion_statuses: list[tuple[str, str]] = field(default_factory=list)
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
        return StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="test-etag",
            initial_bytes=b"\xff\xd8\xfftest",
        )

    def promote_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
        original_name: str,
    ) -> None:
        self.promotion_requests.append((source_key, destination_key))

    def delete_object(self, *, object_key: str) -> None:
        self.deletion_requests.append(object_key)
        if self.observe_upload_id is not None:
            status = MediaAttachment.objects.values_list("status", flat=True).get(
                pk=self.observe_upload_id
            )
            self.deletion_statuses.append((object_key, status))
        if object_key in self.deletion_failures:
            raise MediaStorageOperationError("simulated deletion failure")

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


def _create_score_change(participant_pair) -> ScoreChange:
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=5,
        reason="미디어 정리 테스트",
        resulting_score=5,
    )


def _create_attachment(
    *,
    uploader: Participant,
    status: str,
    expires_at: datetime,
    suffix: str,
    score_change: ScoreChange | None = None,
) -> MediaAttachment:
    upload_id = uuid4()
    finalization_token: UUID | None = None
    actual_size: int | None = None
    finalized_at: datetime | None = None
    if status in (
        MediaAttachment.Status.FINALIZING,
        MediaAttachment.Status.RECLAIMING,
    ):
        finalization_token = uuid4()
    elif status in (MediaAttachment.Status.READY, MediaAttachment.Status.ATTACHED):
        actual_size = 512
        finalized_at = timezone.now()

    object_prefix = (
        "pending"
        if status
        in (
            MediaAttachment.Status.PENDING,
            MediaAttachment.Status.FINALIZING,
            MediaAttachment.Status.RECLAIMING,
            MediaAttachment.Status.DELETING,
        )
        else "media"
    )
    return MediaAttachment.objects.create(
        id=upload_id,
        uploader=uploader,
        score_change=score_change,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=status,
        object_key=f"{object_prefix}/{upload_id}/{suffix}",
        original_name=f"{suffix}.jpg",
        content_type="image/jpeg",
        expected_size=512,
        actual_size=actual_size,
        etag=f"etag-{suffix}" if actual_size is not None else "",
        expires_at=expires_at,
        finalized_at=finalized_at,
        finalization_token=finalization_token,
    )


def test_cleanup_deletes_expired_uploads_in_each_unattached_status(
    participant_pair,
):
    cutoff = timezone.now()
    attachments = tuple(
        _create_attachment(
            uploader=participant_pair.first,
            status=status,
            expires_at=cutoff - timedelta(seconds=1),
            suffix=status,
        )
        for status in (
            MediaAttachment.Status.PENDING,
            MediaAttachment.Status.FINALIZING,
            MediaAttachment.Status.RECLAIMING,
            MediaAttachment.Status.READY,
            MediaAttachment.Status.DELETING,
        )
    )
    storage = FakeMediaStorage()

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=5, deleted=5, failed=0)
    assert not MediaAttachment.objects.filter(
        pk__in=[attachment.pk for attachment in attachments]
    ).exists()
    assert {attachment.object_key for attachment in attachments}.issubset(
        storage.deletion_requests
    )


def test_cleanup_ignores_unexpired_unattached_uploads(participant_pair):
    cutoff = timezone.now()
    attachments = tuple(
        _create_attachment(
            uploader=participant_pair.first,
            status=status,
            expires_at=cutoff + timedelta(seconds=1),
            suffix=f"future-{status}",
        )
        for status in (
            MediaAttachment.Status.PENDING,
            MediaAttachment.Status.FINALIZING,
            MediaAttachment.Status.READY,
        )
    )
    storage = FakeMediaStorage()

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=0, deleted=0, failed=0)
    assert MediaAttachment.objects.filter(
        pk__in=[attachment.pk for attachment in attachments]
    ).count() == len(attachments)
    assert storage.deletion_requests == []


def test_finalizing_cleanup_deletes_pending_and_token_scoped_final_objects(
    participant_pair,
):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.FINALIZING,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="finalizing",
    )
    assert attachment.finalization_token is not None
    storage = FakeMediaStorage(observe_upload_id=attachment.pk)

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=1, deleted=1, failed=0)
    assert set(storage.deletion_requests) == {
        attachment.object_key,
        f"media/{attachment.pk}/{attachment.finalization_token}",
    }
    assert {status for _, status in storage.deletion_statuses} == {
        MediaAttachment.Status.DELETING
    }
    assert not MediaAttachment.objects.filter(pk=attachment.pk).exists()


def test_cleanup_never_deletes_attached_media(participant_pair):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.ATTACHED,
        expires_at=cutoff - timedelta(days=1),
        suffix="attached",
        score_change=_create_score_change(participant_pair),
    )
    storage = FakeMediaStorage()

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=0, deleted=0, failed=0)
    assert MediaAttachment.objects.filter(pk=attachment.pk).exists()
    assert storage.deletion_requests == []


def test_storage_deletion_failure_preserves_attachment_row(participant_pair):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.READY,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="delete-failure",
    )
    storage = FakeMediaStorage(deletion_failures={attachment.object_key})

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=1, deleted=0, failed=1)
    assert storage.deletion_requests == [attachment.object_key]
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING


def test_partial_finalizing_cleanup_resumes_from_its_deleting_tombstone(
    participant_pair,
):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.FINALIZING,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="partial-delete",
    )
    assert attachment.finalization_token is not None
    token_key = f"media/{attachment.pk}/{attachment.finalization_token}"
    storage = FakeMediaStorage(deletion_failures={token_key})

    first_result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert first_result == ExpiredMediaCleanupResult(scanned=1, deleted=0, failed=1)
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
    assert attachment.finalization_token is not None
    assert storage.deletion_requests == [attachment.object_key, token_key]

    storage.deletion_failures.clear()
    retry_result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert retry_result == ExpiredMediaCleanupResult(scanned=1, deleted=1, failed=0)
    assert storage.deletion_requests == [
        attachment.object_key,
        token_key,
        attachment.object_key,
        token_key,
    ]
    assert not MediaAttachment.objects.filter(pk=attachment.pk).exists()


def test_cleanup_finish_preserves_a_newer_discard_grace(participant_pair):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.FINALIZING,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="cleanup-discard-race",
    )
    assert attachment.finalization_token is not None
    token_key = f"media/{attachment.pk}/{attachment.finalization_token}"
    discard_storage = FakeMediaStorage()

    class DiscardDuringCleanupStorage(FakeMediaStorage):
        did_discard = False

        def delete_object(self, *, object_key: str) -> None:
            super().delete_object(object_key=object_key)
            if self.did_discard:
                return
            self.did_discard = True
            discard_media_upload(
                upload_id=attachment.pk,
                uploader=participant_pair.first,
                storage=discard_storage,
            )

    cleanup_storage = DiscardDuringCleanupStorage()
    discard_started_before = timezone.now()

    result = cleanup_expired_media_uploads(
        cutoff=cutoff,
        storage=cleanup_storage,
    )

    assert result == ExpiredMediaCleanupResult(scanned=1, deleted=0, failed=0)
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
    assert attachment.finalization_token is not None
    assert attachment.expires_at >= discard_started_before + timedelta(
        seconds=FINALIZATION_LEASE_SECONDS
    )
    assert cleanup_storage.deletion_requests == [attachment.object_key, token_key]
    assert discard_storage.deletion_requests == [
        f"pending/{attachment.pk}",
        attachment.object_key,
        token_key,
    ]


def test_cleanup_does_not_delete_r2_when_tombstone_write_fails(
    participant_pair,
    monkeypatch,
):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.READY,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="tombstone-write-failure",
    )
    storage = FakeMediaStorage()

    def fail_deleting_save(
        candidate: MediaAttachment,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        assert candidate.pk == attachment.pk
        assert candidate.status == MediaAttachment.Status.DELETING
        raise RuntimeError("simulated tombstone write failure")

    monkeypatch.setattr(MediaAttachment, "save", fail_deleting_save)

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=1, deleted=0, failed=1)
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.READY
    assert storage.deletion_requests == []


def test_cleanup_keeps_tombstone_when_database_finish_fails(
    participant_pair,
    monkeypatch,
):
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.READY,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="database-finish-failure",
    )
    storage = FakeMediaStorage(observe_upload_id=attachment.pk)

    def fail_attachment_delete(
        candidate: MediaAttachment,
        *_args: object,
        **_kwargs: object,
    ) -> tuple[int, dict[str, int]]:
        assert candidate.pk == attachment.pk
        assert candidate.status == MediaAttachment.Status.DELETING
        raise RuntimeError("simulated database finish failure")

    monkeypatch.setattr(MediaAttachment, "delete", fail_attachment_delete)

    result = cleanup_expired_media_uploads(cutoff=cutoff, storage=storage)

    assert result == ExpiredMediaCleanupResult(scanned=1, deleted=0, failed=1)
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
    assert storage.deletion_requests == [attachment.object_key]
    assert storage.deletion_statuses == [
        (attachment.object_key, MediaAttachment.Status.DELETING)
    ]


def test_cleanup_command_check_performs_no_storage_or_database_writes(
    participant_pair,
    monkeypatch,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    cutoff = timezone.now()
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.PENDING,
        expires_at=cutoff - timedelta(seconds=1),
        suffix="command-check",
    )
    before = MediaAttachment.objects.values().get(pk=attachment.pk)
    storage = FakeMediaStorage()
    monkeypatch.setattr(
        "apps.ratings.services.media_cleanup.get_media_storage_gateway",
        lambda: storage,
    )
    output = StringIO()

    with CaptureQueriesContext(connection) as queries:
        call_command("cleanup_media_uploads", "--check", stdout=output)

    dml_queries = [
        query["sql"]
        for query in queries.captured_queries
        if _DML_PATTERN.match(query["sql"])
    ]
    assert dml_queries == []
    assert MediaAttachment.objects.values().get(pk=attachment.pk) == before
    assert storage.promotion_requests == []
    assert storage.deletion_requests == []
    assert "만료된 미연결 업로드: 1개" in output.getvalue()


def test_cleanup_command_exits_nonzero_when_any_r2_delete_fails(
    participant_pair,
    monkeypatch,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = _create_attachment(
        uploader=participant_pair.first,
        status=MediaAttachment.Status.READY,
        expires_at=timezone.now() - timedelta(seconds=1),
        suffix="command-failure",
    )
    storage = FakeMediaStorage(deletion_failures={attachment.object_key})
    monkeypatch.setattr(
        "apps.ratings.services.media_cleanup.get_media_storage_gateway",
        lambda: storage,
    )

    with pytest.raises(CommandError, match="실패: 1개"):
        call_command("cleanup_media_uploads", "--limit", "1")

    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
