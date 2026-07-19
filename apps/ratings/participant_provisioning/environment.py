import os
import re

from ..models import Participant
from .contracts import ParticipantSpec, ProvisioningError


def load_specs_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    specifications = []

    for slot in (Participant.Slot.FIRST, Participant.Slot.SECOND):
        name = environ.get(f"PARTICIPANT_{slot}_NAME", "").strip()
        pin = environ.get(f"PARTICIPANT_{slot}_PIN", "")
        if not name:
            raise ProvisioningError(f"PARTICIPANT_{slot}_NAME 환경 변수가 필요합니다.")
        if len(name) > 30:
            raise ProvisioningError("참가자 이름은 30자 이하여야 합니다.")
        if not re.fullmatch(r"[0-9]{4}", pin):
            raise ProvisioningError(f"PARTICIPANT_{slot}_PIN은 숫자 4자리여야 합니다.")
        specifications.append(
            ParticipantSpec(
                slot=slot,
                username=f"participant-{slot}",
                display_name=name,
                pin=pin,
            )
        )

    if specifications[0].display_name == specifications[1].display_name:
        raise ProvisioningError("두 참가자의 이름은 서로 달라야 합니다.")

    return tuple(specifications)
