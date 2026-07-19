from collections.abc import Sequence
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import DiaryEntry, Participant
from .media_uploads import (
    attach_diary_entry_media_uploads,
    replace_diary_entry_media_uploads,
)

MAX_DIARY_ENTRY_CONTENT_LENGTH = 1000


class DiaryEntryPermissionError(PermissionDenied):
    """Raised when a participant tries to mutate another participant's entry."""


class DiaryEntryNotFoundError(LookupError):
    """Raised when an entry disappears before a requested mutation can lock it."""


def _normalize_content(content: str) -> str:
    normalized_content = content.strip()
    if not normalized_content:
        raise ValidationError("일기 내용을 입력해 주세요.")
    if len(normalized_content) > MAX_DIARY_ENTRY_CONTENT_LENGTH:
        raise ValidationError(
            f"일기는 {MAX_DIARY_ENTRY_CONTENT_LENGTH}자 이하로 입력해 주세요."
        )
    return normalized_content


def _ensure_author(*, entry: DiaryEntry, author: Participant) -> None:
    if entry.author_id != author.pk:
        raise DiaryEntryPermissionError("작성자만 일기를 변경할 수 있습니다.")


def _lock_entry(entry: DiaryEntry) -> DiaryEntry:
    if entry.pk is None:
        raise ValidationError("저장되지 않은 일기입니다.")
    try:
        return DiaryEntry.objects.select_for_update().get(pk=entry.pk)
    except DiaryEntry.DoesNotExist as error:
        raise DiaryEntryNotFoundError("일기를 찾을 수 없습니다.") from error


@transaction.atomic
def create_diary_entry(
    *,
    author: Participant,
    content: str,
    media_upload_ids: Sequence[UUID] = (),
) -> DiaryEntry:
    entry = DiaryEntry.objects.create(
        author=author,
        content=_normalize_content(content),
    )
    attach_diary_entry_media_uploads(
        upload_ids=media_upload_ids,
        uploader=author,
        diary_entry=entry,
    )
    return entry


@transaction.atomic
def update_diary_entry(
    *,
    entry: DiaryEntry,
    author: Participant,
    content: str | None = None,
    media_upload_ids: Sequence[UUID] | None = None,
) -> DiaryEntry:
    if content is None and media_upload_ids is None:
        raise ValidationError("수정할 일기 내용이나 첨부 파일을 입력해 주세요.")
    locked_entry = _lock_entry(entry)
    _ensure_author(entry=locked_entry, author=author)

    update_fields = ["updated_at"]
    if content is not None:
        locked_entry.content = _normalize_content(content)
        update_fields.insert(0, "content")
    if media_upload_ids is not None:
        replace_diary_entry_media_uploads(
            upload_ids=media_upload_ids,
            uploader=author,
            diary_entry=locked_entry,
        )

    locked_entry.updated_at = timezone.now()
    locked_entry.save(update_fields=update_fields)
    return locked_entry


@transaction.atomic
def delete_diary_entry(
    *,
    entry: DiaryEntry,
    author: Participant,
) -> None:
    locked_entry = _lock_entry(entry)
    _ensure_author(entry=locked_entry, author=author)
    replace_diary_entry_media_uploads(
        upload_ids=(),
        uploader=author,
        diary_entry=locked_entry,
    )
    locked_entry.delete()
