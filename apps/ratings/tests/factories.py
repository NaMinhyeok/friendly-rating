from django.contrib.auth import get_user_model

from ..models import Participant, RelationshipScore


def create_participant_pair():
    user_model = get_user_model()
    first_user = user_model.objects.create_user(username="participant-1")
    second_user = user_model.objects.create_user(username="participant-2")
    first = Participant.objects.create(
        user=first_user,
        display_name="첫 번째",
        slot=Participant.Slot.FIRST,
    )
    second = Participant.objects.create(
        user=second_user,
        display_name="두 번째",
        slot=Participant.Slot.SECOND,
    )
    first_to_second = RelationshipScore.objects.create(
        source_participant=first,
        target_participant=second,
    )
    second_to_first = RelationshipScore.objects.create(
        source_participant=second,
        target_participant=first,
    )
    return first, second, first_to_second, second_to_first
