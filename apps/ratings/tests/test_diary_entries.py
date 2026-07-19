import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import DiaryEntry, ScoreChange
from ..services import (
    DiaryEntryPermissionError,
    create_diary_entry,
    delete_diary_entry,
    update_diary_entry,
)

pytestmark = pytest.mark.django_db


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


def test_author_can_delete_diary_entry(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="삭제할 기록",
    )

    delete_diary_entry(entry=entry, author=participant_pair.first)

    assert not DiaryEntry.objects.exists()


def test_non_author_cannot_delete_diary_entry(participant_pair):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="작성자의 기록",
    )

    with pytest.raises(DiaryEntryPermissionError):
        delete_diary_entry(entry=entry, author=participant_pair.second)

    assert DiaryEntry.objects.filter(pk=entry.pk).exists()
