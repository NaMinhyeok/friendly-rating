from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from ..models import DiaryEntry, MediaAttachment, ScoreChange, ScoreChangeComment

pytestmark = pytest.mark.django_db


def _create_score_change(participant_pair):
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=5,
        reason="고마운 일이 있었어요",
        resulting_score=5,
    )


@pytest.mark.parametrize("current_score", (-1, 101), ids=("below-zero", "above-100"))
def test_relationship_score_is_constrained_to_zero_through_one_hundred(
    participant_pair,
    current_score,
):
    score = participant_pair.first_to_second
    score.current_score = current_score

    with pytest.raises(IntegrityError), transaction.atomic():
        score.save(update_fields=("current_score",))


def test_relationship_score_requires_different_participants(participant_pair):
    participant_pair.second_to_first.delete()
    score = participant_pair.first_to_second
    score.target_participant = participant_pair.first

    with pytest.raises(IntegrityError), transaction.atomic():
        score.save(update_fields=("target_participant",))


def test_participant_slot_is_constrained_to_a_known_slot(participant_pair):
    participant = participant_pair.first
    participant.slot = 3

    with pytest.raises(IntegrityError), transaction.atomic():
        participant.save(update_fields=("slot",))


@pytest.mark.parametrize("content", ("", "   "), ids=("empty", "whitespace"))
def test_diary_entry_content_cannot_be_blank(participant_pair, content):
    with pytest.raises(IntegrityError), transaction.atomic():
        DiaryEntry.objects.create(
            author=participant_pair.first,
            content=content,
        )


def test_diary_allows_multiple_entries_from_one_author(participant_pair):
    entries = [
        DiaryEntry.objects.create(
            author=participant_pair.first,
            content=content,
        )
        for content in ("아침 기록", "저녁 기록")
    ]

    assert (
        list(DiaryEntry.objects.filter(author=participant_pair.first).order_by("pk"))
        == entries
    )


def test_diary_entries_are_ordered_by_latest_post_time_then_pk(participant_pair):
    entries = [
        DiaryEntry.objects.create(
            author=participant_pair.first,
            content=content,
        )
        for content in ("먼저 쓴 글", "같은 시각의 첫 글", "같은 시각의 두 번째 글")
    ]
    latest_time = timezone.now()
    DiaryEntry.objects.filter(pk=entries[0].pk).update(
        created_at=latest_time - timedelta(minutes=1)
    )
    DiaryEntry.objects.filter(pk__in=(entries[1].pk, entries[2].pk)).update(
        created_at=latest_time
    )

    assert list(DiaryEntry.objects.all()) == [entries[2], entries[1], entries[0]]


def test_diary_entry_protects_its_author(participant_pair):
    DiaryEntry.objects.create(
        author=participant_pair.first,
        content="보존할 기록",
    )

    with pytest.raises(ProtectedError):
        participant_pair.first.delete()


def test_attached_diary_media_requires_only_its_diary_parent(participant_pair):
    entry = DiaryEntry.objects.create(
        author=participant_pair.first,
        content="사진을 남긴 기록",
    )
    finalized_at = timezone.now()

    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        diary_entry=entry,
        purpose=MediaAttachment.Purpose.DIARY_ENTRY,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.ATTACHED,
        object_key="media/diary-valid",
        original_name="기록.webp",
        content_type="image/webp",
        expected_size=1_024,
        actual_size=1_024,
        expires_at=finalized_at + timedelta(hours=1),
        finalized_at=finalized_at,
    )

    assert attachment.diary_entry == entry
    with pytest.raises(ProtectedError):
        entry.delete()


def test_database_rejects_attached_diary_media_without_a_diary_parent(
    participant_pair,
):
    finalized_at = timezone.now()

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaAttachment.objects.create(
            uploader=participant_pair.first,
            purpose=MediaAttachment.Purpose.DIARY_ENTRY,
            kind=MediaAttachment.Kind.IMAGE,
            status=MediaAttachment.Status.ATTACHED,
            object_key="media/diary-missing-parent",
            original_name="기록.webp",
            content_type="image/webp",
            expected_size=1_024,
            actual_size=1_024,
            expires_at=finalized_at + timedelta(hours=1),
            finalized_at=finalized_at,
        )


def test_database_rejects_a_score_parent_on_pending_diary_media(participant_pair):
    change = _create_score_change(participant_pair)

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaAttachment.objects.create(
            uploader=participant_pair.first,
            score_change=change,
            purpose=MediaAttachment.Purpose.DIARY_ENTRY,
            kind=MediaAttachment.Kind.IMAGE,
            status=MediaAttachment.Status.PENDING,
            object_key="pending/diary-wrong-parent",
            original_name="기록.webp",
            content_type="image/webp",
            expected_size=1_024,
            expires_at=timezone.now() + timedelta(hours=1),
        )


def test_score_change_delta_cannot_be_zero(participant_pair):
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreChange.objects.create(
            relationship_score=participant_pair.first_to_second,
            changed_by=participant_pair.first,
            delta=0,
            reason="변경 없음",
            resulting_score=0,
        )


@pytest.mark.parametrize(
    "resulting_score",
    (-1, 101),
    ids=("below-zero", "above-100"),
)
def test_score_change_result_is_constrained_to_zero_through_one_hundred(
    participant_pair,
    resulting_score,
):
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreChange.objects.create(
            relationship_score=participant_pair.first_to_second,
            changed_by=participant_pair.first,
            delta=1,
            reason="범위를 벗어난 결과",
            resulting_score=resulting_score,
        )


def test_existing_score_change_cannot_be_saved(participant_pair):
    change = _create_score_change(participant_pair)
    change.reason = "바꾼 이유"

    with pytest.raises(ValidationError):
        change.save()


def test_existing_score_change_cannot_be_deleted(participant_pair):
    change = _create_score_change(participant_pair)

    with pytest.raises(ValidationError):
        change.delete()


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_score_changes_cannot_be_changed_in_bulk(participant_pair, operation):
    change = _create_score_change(participant_pair)
    queryset = ScoreChange.objects.filter(pk=change.pk)

    with pytest.raises(ValidationError):
        if operation == "update":
            queryset.update(reason="바꾼 이유")
        else:
            queryset.delete()


def test_score_change_comment_content_cannot_be_empty(participant_pair):
    change = _create_score_change(participant_pair)

    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreChangeComment.objects.create(
            score_change=change,
            author=participant_pair.second,
            content="",
        )
