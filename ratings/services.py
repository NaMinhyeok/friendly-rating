import logging
from functools import partial

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Participant, RelationshipScore, ScoreChange
from .notifications import send_score_change_notification

logger = logging.getLogger(__name__)


def _notify_recipient_after_commit(recipient_id: int) -> None:
    try:
        send_score_change_notification(recipient_id=recipient_id)
    except Exception:
        logger.exception(
            "Unexpected error while dispatching a score-change notification.",
            extra={"recipient_id": recipient_id},
        )


@transaction.atomic
def change_relationship_score(
    *,
    source_participant: Participant,
    delta: int,
    reason: str = "",
) -> ScoreChange:
    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
        raise ValidationError("변경 점수는 0이 아닌 정수여야 합니다.")

    if not isinstance(reason, str):
        raise ValidationError("변경 이유는 문자열이어야 합니다.")

    normalized_reason = reason.strip()
    if len(normalized_reason) > 200:
        raise ValidationError("변경 이유는 200자 이하여야 합니다.")

    relationship_score = RelationshipScore.objects.select_for_update().get(
        source_participant=source_participant
    )
    resulting_score = relationship_score.current_score + delta
    if not 0 <= resulting_score <= 100:
        raise ValidationError("친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다.")

    relationship_score.current_score = resulting_score
    relationship_score.save(update_fields=("current_score", "updated_at"))

    change = ScoreChange.objects.create(
        relationship_score=relationship_score,
        changed_by=source_participant,
        delta=delta,
        reason=normalized_reason,
        resulting_score=resulting_score,
    )
    transaction.on_commit(
        partial(
            _notify_recipient_after_commit,
            relationship_score.target_participant_id,
        ),
        robust=True,
    )
    return change
