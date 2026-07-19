import re
from dataclasses import dataclass
from typing import TypeGuard

from django.db import transaction
from django.utils import timezone

from ..models import Participant, PushDevice

FIREBASE_INSTALLATION_ID_PATTERN = re.compile(r"^[cdef][A-Za-z0-9_-]{21}$")
MAX_PUSH_DEVICES_PER_PARTICIPANT = 5


def is_valid_firebase_installation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(
        FIREBASE_INSTALLATION_ID_PATTERN.fullmatch(value)
    )


@dataclass(frozen=True, slots=True)
class PushDeviceRegistrationResult:
    device_created: bool


@transaction.atomic
def register_participant_push_device(
    *,
    participant: Participant,
    firebase_installation_id: str,
    user_agent: str = "",
) -> PushDeviceRegistrationResult:
    if not is_valid_firebase_installation_id(firebase_installation_id):
        raise ValueError("Invalid Firebase installation ID.")

    Participant.objects.select_for_update().get(pk=participant.pk)
    _, created = PushDevice.objects.update_or_create(
        firebase_installation_id=firebase_installation_id,
        defaults={
            "participant": participant,
            "is_active": True,
            "user_agent": user_agent[:500],
        },
    )
    PushDevice.objects.filter(
        participant=participant,
        is_active=False,
    ).delete()
    retained_active_ids = list(
        PushDevice.objects.filter(participant=participant, is_active=True)
        .order_by("-updated_at", "-pk")
        .values_list("pk", flat=True)[:MAX_PUSH_DEVICES_PER_PARTICIPANT]
    )
    PushDevice.objects.filter(participant=participant, is_active=True).exclude(
        pk__in=retained_active_ids
    ).delete()
    return PushDeviceRegistrationResult(device_created=created)


def unregister_participant_push_device(
    *,
    participant: Participant,
    firebase_installation_id: str,
) -> None:
    if not is_valid_firebase_installation_id(firebase_installation_id):
        raise ValueError("Invalid Firebase installation ID.")

    PushDevice.objects.filter(
        participant=participant,
        firebase_installation_id=firebase_installation_id,
    ).update(
        is_active=False,
        updated_at=timezone.now(),
    )
