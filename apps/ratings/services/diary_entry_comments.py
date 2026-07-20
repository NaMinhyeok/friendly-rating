import logging
from functools import partial

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import DiaryEntry, DiaryEntryComment, Participant
from ..notifications import send_diary_comment_notification
from .diary_entries import DiaryEntryNotFoundError

logger = logging.getLogger(__name__)

MAX_DIARY_ENTRY_COMMENT_LENGTH = 500


def _notify_participant_after_commit(
    recipient_id: int,
    diary_entry_id: int,
) -> None:
    try:
        send_diary_comment_notification(
            recipient_id=recipient_id,
            diary_entry_id=diary_entry_id,
        )
    except Exception:
        logger.exception(
            "Unexpected error while dispatching a diary-comment notification.",
            extra={
                "recipient_id": recipient_id,
                "diary_entry_id": diary_entry_id,
            },
        )


def _other_participant_id(author: Participant) -> int:
    if author.slot == Participant.Slot.FIRST:
        recipient_slot = Participant.Slot.SECOND
    elif author.slot == Participant.Slot.SECOND:
        recipient_slot = Participant.Slot.FIRST
    else:
        raise ValidationError("올바른 참가자만 댓글을 남길 수 있습니다.")
    return Participant.objects.values_list("pk", flat=True).get(slot=recipient_slot)


@transaction.atomic
def add_diary_entry_comment(
    *,
    diary_entry: DiaryEntry,
    author: Participant,
    content: str,
) -> DiaryEntryComment:
    normalized_content = content.strip()
    if not normalized_content:
        raise ValidationError("댓글 내용을 입력해 주세요.")
    if len(normalized_content) > MAX_DIARY_ENTRY_COMMENT_LENGTH:
        raise ValidationError(
            f"댓글은 {MAX_DIARY_ENTRY_COMMENT_LENGTH}자 이하로 입력해 주세요."
        )

    diary_entry_id = diary_entry.pk
    if diary_entry_id is None:
        raise ValidationError("저장되지 않은 일기입니다.")
    try:
        locked_entry = DiaryEntry.objects.select_for_update().get(pk=diary_entry_id)
    except DiaryEntry.DoesNotExist as error:
        raise DiaryEntryNotFoundError("일기를 찾을 수 없습니다.") from error

    recipient_id = _other_participant_id(author)
    comment = DiaryEntryComment.objects.create(
        diary_entry=locked_entry,
        author=author,
        content=normalized_content,
    )
    transaction.on_commit(
        partial(
            _notify_participant_after_commit,
            recipient_id,
            diary_entry_id,
        ),
        robust=True,
    )
    return comment
