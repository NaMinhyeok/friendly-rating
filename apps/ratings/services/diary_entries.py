from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from ..models import DiaryEntry, Participant

MAX_DIARY_ENTRY_CONTENT_LENGTH = 1000


class DiaryEntryPermissionError(PermissionDenied):
    """Raised when a participant tries to mutate another participant's entry."""


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


def create_diary_entry(
    *,
    author: Participant,
    content: str,
) -> DiaryEntry:
    return DiaryEntry.objects.create(
        author=author,
        content=_normalize_content(content),
    )


def update_diary_entry(
    *,
    entry: DiaryEntry,
    author: Participant,
    content: str,
) -> DiaryEntry:
    _ensure_author(entry=entry, author=author)
    entry.content = _normalize_content(content)
    entry.updated_at = timezone.now()
    entry.save(update_fields=("content", "updated_at"))
    return entry


def delete_diary_entry(
    *,
    entry: DiaryEntry,
    author: Participant,
) -> None:
    _ensure_author(entry=entry, author=author)
    entry.delete()
