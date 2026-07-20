from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from ..models import DiaryEntry, DiaryEntryComment
from ..services import DiaryEntryNotFoundError, add_diary_entry_comment

pytestmark = pytest.mark.django_db


def _entry(participant_pair) -> DiaryEntry:
    return DiaryEntry.objects.create(
        author=participant_pair.first,
        content="함께 나눌 일기",
    )


@pytest.mark.parametrize(
    ("author_attribute", "recipient_attribute"),
    (("first", "second"), ("second", "first")),
)
def test_add_diary_entry_comment_normalizes_content_and_notifies_other_slot_after_commit(
    participant_pair,
    django_capture_on_commit_callbacks,
    author_attribute,
    recipient_attribute,
):
    entry = _entry(participant_pair)
    author = getattr(participant_pair, author_attribute)
    recipient = getattr(participant_pair, recipient_attribute)

    with patch(
        "apps.ratings.services.diary_entry_comments.send_diary_comment_notification"
    ) as send_notification:
        with django_capture_on_commit_callbacks(execute=True):
            comment = add_diary_entry_comment(
                diary_entry=entry,
                author=author,
                content="  우리 같이 이야기해 보자  ",
            )
            send_notification.assert_not_called()

        send_notification.assert_called_once_with(
            recipient_id=recipient.pk,
            diary_entry_id=entry.pk,
        )

    assert comment == DiaryEntryComment.objects.get()
    assert comment.diary_entry == entry
    assert comment.author == author
    assert comment.content == "우리 같이 이야기해 보자"


def test_diary_entry_comment_accepts_exactly_five_hundred_characters(
    participant_pair,
):
    entry = _entry(participant_pair)
    content = "가" * 500

    comment = add_diary_entry_comment(
        diary_entry=entry,
        author=participant_pair.second,
        content=content,
    )

    assert comment.content == content


@pytest.mark.parametrize(
    "content",
    ("", "   ", "가" * 501),
    ids=("empty", "whitespace", "too-long"),
)
def test_invalid_diary_entry_comment_is_rejected_without_writing(
    participant_pair,
    content,
):
    entry = _entry(participant_pair)

    with pytest.raises(ValidationError):
        add_diary_entry_comment(
            diary_entry=entry,
            author=participant_pair.first,
            content=content,
        )

    assert not DiaryEntryComment.objects.exists()


def test_notification_failure_does_not_undo_diary_entry_comment(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    entry = _entry(participant_pair)

    with patch(
        "apps.ratings.services.diary_entry_comments.send_diary_comment_notification",
        side_effect=RuntimeError("FCM unavailable"),
    ) as send_notification:
        with django_capture_on_commit_callbacks(execute=True):
            comment = add_diary_entry_comment(
                diary_entry=entry,
                author=participant_pair.first,
                content="알림과 별개로 남겨 줘",
            )

    assert DiaryEntryComment.objects.filter(pk=comment.pk).exists()
    send_notification.assert_called_once_with(
        recipient_id=participant_pair.second.pk,
        diary_entry_id=entry.pk,
    )


def test_commenting_on_an_entry_deleted_after_read_raises_domain_not_found(
    participant_pair,
):
    entry = _entry(participant_pair)
    stale_entry = DiaryEntry.objects.get(pk=entry.pk)
    DiaryEntry.objects.filter(pk=entry.pk).delete()

    with (
        patch(
            "apps.ratings.services.diary_entry_comments.send_diary_comment_notification"
        ) as send_notification,
        pytest.raises(DiaryEntryNotFoundError, match="찾을 수 없습니다"),
    ):
        add_diary_entry_comment(
            diary_entry=stale_entry,
            author=participant_pair.second,
            content="이미 사라진 글에 남길 댓글",
        )

    assert not DiaryEntryComment.objects.exists()
    send_notification.assert_not_called()
