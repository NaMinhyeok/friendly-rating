import logging
from collections.abc import Sequence
from functools import partial
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from ..models import Participant, ScoreChange, ScoreChangeComment
from ..notifications import send_score_comment_notification
from .media_uploads import (
    MAX_COMMENT_IMAGE_ATTACHMENTS,
    MediaUploadValidationError,
    attach_comment_media_uploads,
)

logger = logging.getLogger(__name__)

MAX_SCORE_COMMENT_LENGTH = 500


def _notify_participant_after_commit(
    recipient_id: int,
    score_change_id: int,
) -> None:
    try:
        send_score_comment_notification(
            recipient_id=recipient_id,
            score_change_id=score_change_id,
        )
    except Exception:
        logger.exception(
            "Unexpected error while dispatching a score-comment notification.",
            extra={
                "recipient_id": recipient_id,
                "score_change_id": score_change_id,
            },
        )


@transaction.atomic
def add_score_change_comment(
    *,
    score_change: ScoreChange,
    author: Participant,
    content: str,
    media_upload_ids: Sequence[UUID] = (),
) -> ScoreChangeComment:
    normalized_content = content.strip()
    normalized_upload_ids = tuple(media_upload_ids)
    if not normalized_content and not normalized_upload_ids:
        raise ValidationError("댓글 내용이나 첨부 파일을 입력해 주세요.")
    if len(normalized_content) > MAX_SCORE_COMMENT_LENGTH:
        raise ValidationError(
            f"댓글은 {MAX_SCORE_COMMENT_LENGTH}자 이하로 입력해 주세요."
        )
    if len(normalized_upload_ids) > MAX_COMMENT_IMAGE_ATTACHMENTS:
        raise MediaUploadValidationError("첨부 파일은 최대 4개까지 올릴 수 있어요.")

    relationship_score = score_change.relationship_score
    participant_ids = {
        relationship_score.source_participant_id,
        relationship_score.target_participant_id,
    }
    if author.pk not in participant_ids:
        raise PermissionDenied("이 점수 변경에 댓글을 남길 수 없습니다.")

    recipient_id = (
        relationship_score.target_participant_id
        if author.pk == relationship_score.source_participant_id
        else relationship_score.source_participant_id
    )
    comment = ScoreChangeComment.objects.create(
        score_change=score_change,
        author=author,
        content=normalized_content,
        media_count=len(normalized_upload_ids),
    )
    attach_comment_media_uploads(
        upload_ids=normalized_upload_ids,
        uploader=author,
        score_change=score_change,
        comment=comment,
    )
    if score_change.pk is None:
        raise RuntimeError("Saved score change has no primary key.")
    transaction.on_commit(
        partial(
            _notify_participant_after_commit,
            recipient_id,
            score_change.pk,
        ),
        robust=True,
    )
    return comment
