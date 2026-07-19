from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Q

from ..models import Participant, RelationshipScore


@dataclass
class ProvisioningSnapshot:
    participants_by_slot: dict[int, Participant]
    users_by_id: dict[int, object]
    canonical_users: dict[str, object]
    scores_by_source_id: dict[int, RelationshipScore]
    participant_count: int
    score_count: int


def load_snapshot(specifications, *, lock):
    participant_query = Participant.objects.order_by("pk")
    score_query = RelationshipScore.objects.order_by("pk")
    if lock:
        participant_query = participant_query.select_for_update()
        score_query = score_query.select_for_update()

    # Score writes lock RelationshipScore before their Participant FK is checked.
    # Keep the same order here to avoid a PostgreSQL deadlock during reconciliation.
    scores = list(score_query)
    participants = list(participant_query)
    participant_user_ids = [participant.user_id for participant in participants]
    canonical_usernames = [spec.username for spec in specifications]

    user_query = (
        get_user_model()
        .objects.filter(
            Q(pk__in=participant_user_ids) | Q(username__in=canonical_usernames)
        )
        .order_by("pk")
    )
    if lock:
        user_query = user_query.select_for_update()
    users = list(user_query)

    return ProvisioningSnapshot(
        participants_by_slot={
            participant.slot: participant for participant in participants
        },
        users_by_id={user.pk: user for user in users},
        canonical_users={
            user.username: user
            for user in users
            if user.username in canonical_usernames
        },
        scores_by_source_id={score.source_participant_id: score for score in scores},
        participant_count=len(participants),
        score_count=len(scores),
    )
