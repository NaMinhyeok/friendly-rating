from django.core.management.base import BaseCommand, CommandError

from ratings.participant_provisioning import (
    ProvisioningError,
    ProvisioningMode,
    ProvisioningOutcome,
    load_specs_from_environment,
    provision_participants,
)


class Command(BaseCommand):
    help = "환경 변수에 맞춰 두 참가자와 양방향 친밀도 점수를 설정합니다."

    def add_arguments(self, parser):
        mode_group = parser.add_mutually_exclusive_group()
        mode_group.add_argument(
            "--check",
            action="store_true",
            help="현재 설정을 변경하지 않고 환경 변수와 일치하는지 확인합니다.",
        )
        mode_group.add_argument(
            "--reconcile",
            action="store_true",
            help="안전하게 복구 가능한 설정 차이를 명시적으로 반영합니다.",
        )

    def handle(self, *args, **options):
        if options["check"] and options["reconcile"]:
            raise CommandError("--check와 --reconcile은 함께 사용할 수 없습니다.")
        if options["check"]:
            mode = ProvisioningMode.CHECK
        elif options["reconcile"]:
            mode = ProvisioningMode.RECONCILE
        else:
            mode = ProvisioningMode.DEFAULT

        try:
            specifications = load_specs_from_environment()
            result = provision_participants(specifications, mode=mode)
        except ProvisioningError as error:
            raise CommandError(str(error)) from error

        messages = {
            ProvisioningOutcome.BOOTSTRAPPED: "참가자 최초 설정을 완료했습니다.",
            ProvisioningOutcome.UNCHANGED: (
                "참가자 설정이 일치합니다. 변경하지 않았습니다."
            ),
            ProvisioningOutcome.RECONCILED: (
                "참가자 설정 차이를 안전하게 반영했습니다."
            ),
        }
        self.stdout.write(self.style.SUCCESS(messages[result.outcome]))
