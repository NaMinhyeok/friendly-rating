import logging
from collections.abc import Sequence
from functools import partial
from uuid import UUID

from django.db import transaction

from ..models import Participant, RelationshipScore, ScoreChange
from ..notifications import send_score_change_notification
from ..score_rules import (
    PreparedScoreChange,
    calculate_resulting_score,
    prepare_score_change,
    prepare_target_score_change,
)
from .media_uploads import attach_score_change_media_uploads

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


def _locked_relationship_score(
    *,
    source_participant: Participant,
) -> RelationshipScore:
    return RelationshipScore.objects.select_for_update().get(
        source_participant=source_participant
    )


def _persist_score_change(
    *,
    relationship_score: RelationshipScore,
    source_participant: Participant,
    prepared_change: PreparedScoreChange,
    resulting_score: int,
    media_upload_ids: Sequence[UUID],
) -> ScoreChange:
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
    attach_score_change_media_uploads(
        upload_ids=media_upload_ids,
        uploader=source_participant,
        score_change=change,
    )
    transaction.on_commit(
        partial(
            _notify_recipient_after_commit,
            relationship_score.target_participant_id,
            change.pk,
        ),
        robust=True,
    )
    return change


@transaction.atomic
def change_relationship_score(
    *,
    source_participant: Participant,
    delta: int,
    reason: str = "",
    media_upload_ids: Sequence[UUID] = (),
) -> ScoreChange:
    prepared_change = prepare_score_change(delta=delta, reason=reason)

    relationship_score = _locked_relationship_score(
        source_participant=source_participant,
    )
    resulting_score = calculate_resulting_score(
        current_score=relationship_score.current_score,
        change=prepared_change,
    )

    return _persist_score_change(
        relationship_score=relationship_score,
        source_participant=source_participant,
        prepared_change=prepared_change,
        resulting_score=resulting_score,
        media_upload_ids=media_upload_ids,
    )


@transaction.atomic
def set_relationship_score(
    *,
    source_participant: Participant,
    target_score: int,
    reason: str = "",
    media_upload_ids: Sequence[UUID] = (),
) -> ScoreChange:
    relationship_score = _locked_relationship_score(
        source_participant=source_participant,
    )
    prepared_change = prepare_target_score_change(
        current_score=relationship_score.current_score,
        target_score=target_score,
        reason=reason,
    )
    resulting_score = calculate_resulting_score(
        current_score=relationship_score.current_score,
        change=prepared_change,
    )

    return _persist_score_change(
        relationship_score=relationship_score,
        source_participant=source_participant,
        prepared_change=prepared_change,
        resulting_score=resulting_score,
        media_upload_ids=media_upload_ids,
    )
