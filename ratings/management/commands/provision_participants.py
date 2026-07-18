import os
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ratings.models import Participant, RelationshipScore


class Command(BaseCommand):
    help = "환경 변수에서 두 참가자와 양방향 친밀도 점수를 생성합니다."

    @transaction.atomic
    def handle(self, *args, **options):
        specifications = []

        for slot in (Participant.Slot.FIRST, Participant.Slot.SECOND):
            name = os.getenv(f"PARTICIPANT_{slot}_NAME", "").strip()
            pin = os.getenv(f"PARTICIPANT_{slot}_PIN", "")
            if not name:
                raise CommandError(f"PARTICIPANT_{slot}_NAME 환경 변수가 필요합니다.")
            if len(name) > 30:
                raise CommandError("참가자 이름은 30자 이하여야 합니다.")
            if not re.fullmatch(r"\d{4}", pin):
                raise CommandError(
                    f"PARTICIPANT_{slot}_PIN은 숫자 4자리여야 합니다."
                )
            specifications.append((slot, name, pin))

        if specifications[0][1] == specifications[1][1]:
            raise CommandError("두 참가자의 이름은 서로 달라야 합니다.")

        user_model = get_user_model()
        participants = []
        for slot, name, pin in specifications:
            user, _ = user_model.objects.get_or_create(username=f"participant-{slot}")
            user.first_name = name
            user.is_active = True
            user.is_staff = False
            user.is_superuser = False
            if not user.check_password(pin):
                user.set_password(pin)
            user.save()

            participant, _ = Participant.objects.update_or_create(
                slot=slot,
                defaults={
                    "user": user,
                    "display_name": name,
                },
            )
            participants.append(participant)

        first, second = participants
        RelationshipScore.objects.update_or_create(
            rater=first,
            defaults={"recipient": second},
        )
        RelationshipScore.objects.update_or_create(
            rater=second,
            defaults={"recipient": first},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"참가자 {first.display_name}, {second.display_name} 설정을 완료했습니다."
            )
        )
