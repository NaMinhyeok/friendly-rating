import logging
from functools import partial

from django.db import transaction

from ..models import Participant, RelationshipScore, ScoreChange
from ..notifications import send_score_change_notification
from ..score_rules import calculate_resulting_score, prepare_score_change

logger = logging.getLogger(__name__)


def _notify_recipient_after_commit(
    recipient_id: int,
    score_change_id: int,
) -> None:
    try:
        send_score_change_notification(
            recipient_id=recipient_id,
            score_change_id=score_change_id,
        )
    except Exception:
        logger.exception(
            "Unexpected error while dispatching a score-change notification.",
            extra={
                "recipient_id": recipient_id,
                "score_change_id": score_change_id,
            },
        )


@transaction.atomic
def change_relationship_score(
    *,
    source_participant: Participant,
    delta: int,
    reason: str = "",
) -> ScoreChange:
    prepared_change = prepare_score_change(delta=delta, reason=reason)

    relationship_score = RelationshipScore.objects.select_for_update().get(
        source_participant=source_participant
    )
    resulting_score = calculate_resulting_score(
        current_score=relationship_score.current_score,
        change=prepared_change,
    )

    relationship_score.current_score = resulting_score
    relationship_score.save(update_fields=("current_score", "updated_at"))

    change = ScoreChange.objects.create(
        relationship_score=relationship_score,
        changed_by=source_participant,
        delta=prepared_change.delta,
        reason=prepared_change.reason,
        resulting_score=resulting_score,
    )
    if change.pk is None:
        raise RuntimeError("Saved score change has no primary key.")
    transaction.on_commit(
        partial(
            _notify_recipient_after_commit,
            relationship_score.target_participant_id,
            change.pk,
        ),
        robust=True,
    )
    return change
