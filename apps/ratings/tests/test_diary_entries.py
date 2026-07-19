from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import DiaryEntry, MediaAttachment, ScoreChange
from ..services import (
    DiaryEntryNotFoundError,
    DiaryEntryPermissionError,
    MediaUploadPermissionError,
    MediaUploadValidationError,
    create_diary_entry,
    delete_diary_entry,
    update_diary_entry,
)

pytestmark = pytest.mark.django_db


def _ready_diary_attachment(
    *,
    uploader,
    suffix: str,
    kind: str = MediaAttachment.Kind.IMAGE,
) -> MediaAttachment:
    byte_size = 1_024 if kind == MediaAttachment.Kind.IMAGE else 4_096
    content_type = "image/webp" if kind == MediaAttachment.Kind.IMAGE else "video/mp4"
    finalized_at = timezone.now()
    return MediaAttachment.objects.create(
        uploader=uploader,
        purpose=MediaAttachment.Purpose.DIARY_ENTRY,
        kind=kind,
        status=MediaAttachment.Status.READY,
        object_key=f"media/diary-{suffix}",
        original_name=f"{suffix}.bin",
        content_type=content_type,
        expected_size=byte_size,
        actual_size=byte_size,
        etag=f"etag-{suffix}",
        expires_at=finalized_at + timedelta(hours=1),
        finalized_at=finalized_at,
    )


def test_create_diary_entry_posts_now_and_normalizes_content_without_changing_scores(
    participant_pair,
):
    before_creation = timezone.now()

    entry = create_diary_entry(
        author=participant_pair.first,
        content="  오늘 함께 산책해서 좋았어  ",
    )

    after_creation = timezone.now()
    assert entry == DiaryEntry.objects.get()
    assert entry.author == participant_pair.first
    assert entry.content == "오늘 함께 산책해서 좋았어"
    assert before_creation <= entry.created_at <= after_creation
    assert entry.updated_at is None
    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert participant_pair.second_to_first.current_score == 0
    assert not ScoreChange.objects.exists()


def test_create_diary_entry_atomically_consumes_media_in_requested_order(
    participant_pair,
):
    first = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="first",
    )
    second = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="second",
    )

    entry = create_diary_entry(
        author=participant_pair.first,
        content="사진과 함께 남긴 기록",
        media_upload_ids=(second.pk, first.pk),
    )

    attached = list(
        MediaAttachment.objects.filter(diary_entry=entry).order_by("position")
    )
    assert [attachment.pk for attachment in attached] == [second.pk, first.pk]
    assert {attachment.status for attachment in attached} == {
        MediaAttachment.Status.ATTACHED
    }


def test_invalid_diary_media_rolls_back_entry_and_upload_state(participant_pair):
    image = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="mixed-image",
    )
    video = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="mixed-video",
        kind=MediaAttachment.Kind.VIDEO,
    )

    with pytest.raises(MediaUploadValidationError, match="함께 올릴 수 없어요"):
        create_diary_entry(
            author=participant_pair.first,
            content="저장되지 않을 기록",
            media_upload_ids=(image.pk, video.pk),
        )

    assert not DiaryEntry.objects.exists()
    assert set(MediaAttachment.objects.values_list("status", flat=True)) == {
        MediaAttachment.Status.READY
    }


def test_diary_rejects_more_than_one_video_without_writing(participant_pair):
    videos = tuple(
        _ready_diary_attachment(
            uploader=participant_pair.first,
            suffix=f"video-{index}",
            kind=MediaAttachment.Kind.VIDEO,
        )
        for index in range(2)
    )

    with pytest.raises(MediaUploadValidationError, match="영상은 한 개만"):
        create_diary_entry(
            author=participant_pair.first,
            content="영상이 너무 많은 기록",
            media_upload_ids=tuple(video.pk for video in videos),
        )

    assert not DiaryEntry.objects.exists()
    assert all(
        status == MediaAttachment.Status.READY
        for status in MediaAttachment.objects.values_list("status", flat=True)
    )


@pytest.mark.parametrize(
    "content",
    ("", "   ", "가" * 1001),
    ids=("empty", "whitespace", "too-long"),
)
def test_create_diary_entry_rejects_invalid_content_without_writing(
    participant_pair,
    content,
):
    with pytest.raises(ValidationError):
        create_diary_entry(
            author=participant_pair.first,
            content=content,
        )

    assert not DiaryEntry.objects.exists()


def test_author_can_update_content_without_changing_publish_time(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="수정 전",
    )
    created_at = entry.created_at
    before_update = timezone.now()

    returned_entry = update_diary_entry(
        entry=entry,
        author=participant_pair.first,
        content="  수정한 내용  ",
    )

    entry.refresh_from_db()
    assert returned_entry == entry
    assert entry.content == "수정한 내용"
    assert entry.created_at == created_at
    assert entry.updated_at is not None
    assert entry.updated_at >= before_update


@pytest.mark.parametrize(
    "content",
    ("", "가" * 1001),
    ids=("empty", "too-long"),
)
def test_invalid_update_preserves_the_existing_entry(participant_pair, content):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="기존 내용",
    )
    previous_updated_at = entry.updated_at

    with pytest.raises(ValidationError):
        update_diary_entry(
            entry=entry,
            author=participant_pair.first,
            content=content,
        )

    entry.refresh_from_db()
    assert entry.content == "기존 내용"
    assert entry.updated_at == previous_updated_at


def test_non_author_cannot_update_diary_entry(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="작성자의 기록",
    )

    with pytest.raises(DiaryEntryPermissionError):
        update_diary_entry(
            entry=entry,
            author=participant_pair.second,
            content="다른 사람이 바꾼 내용",
        )

    entry.refresh_from_db()
    assert entry.content == "작성자의 기록"


def test_update_reports_an_entry_deleted_before_the_row_lock(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="곧 삭제될 기록",
    )
    DiaryEntry.objects.filter(pk=entry.pk).delete()

    with pytest.raises(DiaryEntryNotFoundError, match="찾을 수 없습니다"):
        update_diary_entry(
            entry=entry,
            author=participant_pair.first,
            content="수정되지 않을 내용",
        )


def test_update_replaces_diary_media_with_existing_and_new_uploads_idempotently(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    removed = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="removed",
    )
    kept = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="kept",
    )
    entry = create_diary_entry(
        author=participant_pair.first,
        content="수정 전",
        media_upload_ids=(removed.pk, kept.pk),
    )
    added = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="added",
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        updated = update_diary_entry(
            entry=entry,
            author=participant_pair.first,
            content="수정 후",
            media_upload_ids=(kept.pk, added.pk),
        )

    assert len(callbacks) == 1
    removed.refresh_from_db()
    kept.refresh_from_db()
    added.refresh_from_db()
    assert updated.content == "수정 후"
    assert removed.diary_entry_id is None
    assert removed.status == MediaAttachment.Status.DELETING
    assert removed.expires_at <= timezone.now()
    assert (kept.diary_entry_id, kept.status, kept.position) == (
        entry.pk,
        MediaAttachment.Status.ATTACHED,
        0,
    )
    assert (added.diary_entry_id, added.status, added.position) == (
        entry.pk,
        MediaAttachment.Status.ATTACHED,
        1,
    )

    with django_capture_on_commit_callbacks(execute=False) as retry_callbacks:
        update_diary_entry(
            entry=entry,
            author=participant_pair.first,
            media_upload_ids=(kept.pk, added.pk),
        )

    assert retry_callbacks == []
    assert list(
        MediaAttachment.objects.filter(
            diary_entry=entry,
            status=MediaAttachment.Status.ATTACHED,
        ).values_list("pk", flat=True)
    ) == [kept.pk, added.pk]


def test_invalid_replacement_rolls_back_content_and_media(participant_pair):
    existing = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="existing",
    )
    entry = create_diary_entry(
        author=participant_pair.first,
        content="기존 내용",
        media_upload_ids=(existing.pk,),
    )
    another_participants_upload = _ready_diary_attachment(
        uploader=participant_pair.second,
        suffix="other-owner",
    )

    with pytest.raises(MediaUploadPermissionError):
        update_diary_entry(
            entry=entry,
            author=participant_pair.first,
            content="반영되면 안 되는 내용",
            media_upload_ids=(another_participants_upload.pk,),
        )

    entry.refresh_from_db()
    existing.refresh_from_db()
    another_participants_upload.refresh_from_db()
    assert entry.content == "기존 내용"
    assert existing.diary_entry_id == entry.pk
    assert existing.status == MediaAttachment.Status.ATTACHED
    assert another_participants_upload.status == MediaAttachment.Status.READY


def test_author_can_delete_diary_entry(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="삭제할 기록",
    )

    delete_diary_entry(entry=entry, author=participant_pair.first)

    assert not DiaryEntry.objects.exists()


def test_delete_retires_attached_media_before_removing_entry(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    attachment = _ready_diary_attachment(
        uploader=participant_pair.first,
        suffix="entry-delete",
    )
    entry = create_diary_entry(
        author=participant_pair.first,
        content="첨부와 함께 삭제할 기록",
        media_upload_ids=(attachment.pk,),
    )
    entry_id = entry.pk

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        delete_diary_entry(entry=entry, author=participant_pair.first)

    assert len(callbacks) == 1
    assert not DiaryEntry.objects.filter(pk=entry_id).exists()
    attachment.refresh_from_db()
    assert attachment.diary_entry_id is None
    assert attachment.status == MediaAttachment.Status.DELETING
    assert attachment.expires_at <= timezone.now()


def test_non_author_cannot_delete_diary_entry(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="작성자의 기록",
    )

    with pytest.raises(DiaryEntryPermissionError):
        delete_diary_entry(entry=entry, author=participant_pair.second)

    assert DiaryEntry.objects.filter(pk=entry.pk).exists()


def test_delete_reports_an_entry_deleted_before_the_row_lock(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="이미 삭제된 기록",
    )
    DiaryEntry.objects.filter(pk=entry.pk).delete()

    with pytest.raises(DiaryEntryNotFoundError, match="찾을 수 없습니다"):
        delete_diary_entry(entry=entry, author=participant_pair.first)
