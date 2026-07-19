from typing import cast
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

from ..models import Participant, RelationshipScore
from .contracts import PasswordStatus
from .inspection import get_password_status


def bootstrap_participants(specifications):
    user_model = cast(type[User], get_user_model())
    participants = []
    for spec in specifications:
        user = user_model.objects.create_user(
            username=spec.username,
            first_name=spec.display_name,
            password=spec.pin,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        participants.append(
            Participant.objects.create(
                user=user,
                display_name=spec.display_name,
                slot=spec.slot,
            )
        )

    RelationshipScore.objects.bulk_create(
        [
            RelationshipScore(
                source_participant=participants[0],
                target_participant=participants[1],
            ),
            RelationshipScore(
                source_participant=participants[1],
                target_participant=participants[0],
            ),
        ]
    )


def reconcile_participants(specifications, snapshot):
    participants = [snapshot.participants_by_slot[spec.slot] for spec in specifications]

    for spec, participant in zip(specifications, participants, strict=True):
        user = snapshot.users_by_id[participant.user_id]
        update_fields = []
        desired_fields = {
            "username": spec.username,
            "first_name": spec.display_name,
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        }
        for field_name, desired_value in desired_fields.items():
            if getattr(user, field_name) != desired_value:
                setattr(user, field_name, desired_value)
                update_fields.append(field_name)

        if get_password_status(user.password, spec.pin) != PasswordStatus.EXACT:
            user.set_password(spec.pin)
            update_fields.append("password")

        if update_fields:
            user.save(update_fields=update_fields)

    changed_participants = [
        (spec, participant)
        for spec, participant in zip(specifications, participants, strict=True)
        if participant.display_name != spec.display_name
    ]
    for _, participant in changed_participants:
        participant.display_name = _temporary_display_name()
        participant.save(update_fields=["display_name"])
    for spec, participant in changed_participants:
        participant.display_name = spec.display_name
        participant.save(update_fields=["display_name"])

    expected_target_by_source = {
        participants[0].pk: participants[1],
        participants[1].pk: participants[0],
    }
    for source in participants:
        target = expected_target_by_source[source.pk]
        score = snapshot.scores_by_source_id.get(source.pk)
        if score is None:
            RelationshipScore.objects.create(
                source_participant=source,
                target_participant=target,
            )


def _temporary_display_name():
    while True:
        candidate = f"__provision_{uuid4().hex[:16]}"
        if not Participant.objects.filter(display_name=candidate).exists():
            return candidate
