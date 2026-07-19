import os
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, get_hasher, identify_hasher
from django.db import IntegrityError, transaction
from django.db.models import Q

from ratings.models import Participant, RelationshipScore


class ProvisioningError(Exception):
    """Raised when participant provisioning cannot proceed safely."""


class ProvisioningMode(StrEnum):
    DEFAULT = "default"
    CHECK = "check"
    RECONCILE = "reconcile"


class ProvisioningState(StrEnum):
    EMPTY = "empty"
    EXACT = "exact"
    DRIFT = "drift"
    UNSAFE_DRIFT = "unsafe_drift"


class ProvisioningOutcome(StrEnum):
    BOOTSTRAPPED = "bootstrapped"
    UNCHANGED = "unchanged"
    RECONCILED = "reconciled"


class PasswordStatus(StrEnum):
    EXACT = "exact"
    MISMATCH = "mismatch"
    OUTDATED_HASH = "outdated_hash"


@dataclass(frozen=True)
class ParticipantSpec:
    slot: int
    username: str
    display_name: str
    pin: str


@dataclass(frozen=True)
class DriftIssue:
    code: str
    reconcilable: bool
    message: str


@dataclass(frozen=True)
class Inspection:
    state: ProvisioningState
    issues: tuple[DriftIssue, ...]


@dataclass(frozen=True)
class ProvisioningResult:
    outcome: ProvisioningOutcome


@dataclass
class _Snapshot:
    participants_by_slot: dict[int, Participant]
    users_by_id: dict[int, object]
    canonical_users: dict[str, object]
    scores_by_source_id: dict[int, RelationshipScore]
    participant_count: int
    score_count: int


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


def inspect_provisioning(specifications):
    snapshot = _load_snapshot(specifications, lock=False)
    return _inspect_snapshot(specifications, snapshot)


def provision_participants(specifications, *, mode=ProvisioningMode.DEFAULT):
    mode = ProvisioningMode(mode)

    if mode == ProvisioningMode.CHECK:
        inspection = inspect_provisioning(specifications)
        if inspection.state != ProvisioningState.EXACT:
            raise ProvisioningError(_inspection_error_message(inspection))
        return ProvisioningResult(ProvisioningOutcome.UNCHANGED)

    if mode == ProvisioningMode.DEFAULT:
        inspection = inspect_provisioning(specifications)
        if inspection.state == ProvisioningState.EXACT:
            return ProvisioningResult(ProvisioningOutcome.UNCHANGED)
        if inspection.state != ProvisioningState.EMPTY:
            raise ProvisioningError(_inspection_error_message(inspection))

    try:
        with transaction.atomic():
            snapshot = _load_snapshot(specifications, lock=True)
            inspection = _inspect_snapshot(specifications, snapshot)

            if inspection.state == ProvisioningState.EXACT:
                result = ProvisioningResult(ProvisioningOutcome.UNCHANGED)
            elif inspection.state == ProvisioningState.EMPTY:
                _bootstrap(specifications)
                result = ProvisioningResult(ProvisioningOutcome.BOOTSTRAPPED)
            elif mode == ProvisioningMode.RECONCILE and all(
                issue.reconcilable for issue in inspection.issues
            ):
                _reconcile(specifications, snapshot)
                result = ProvisioningResult(ProvisioningOutcome.RECONCILED)
            else:
                raise ProvisioningError(_inspection_error_message(inspection))
    except IntegrityError as error:
        raise ProvisioningError(
            "동시 실행 또는 데이터 충돌을 감지했습니다. 변경 상태를 확인해 주세요."
        ) from error

    return result


def _load_snapshot(specifications, *, lock):
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

    return _Snapshot(
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


def _inspect_snapshot(specifications, snapshot):
    if (
        snapshot.participant_count == 0
        and snapshot.score_count == 0
        and not snapshot.canonical_users
    ):
        return Inspection(ProvisioningState.EMPTY, ())

    expected_slots = {spec.slot for spec in specifications}
    if (
        snapshot.participant_count != len(specifications)
        or set(snapshot.participants_by_slot) != expected_slots
    ):
        return Inspection(
            ProvisioningState.UNSAFE_DRIFT,
            (
                DriftIssue(
                    code="participant_graph_incomplete",
                    reconcilable=False,
                    message="참가자 소유 관계가 불완전합니다.",
                ),
            ),
        )

    issues = []
    participants = []
    for spec in specifications:
        participant = snapshot.participants_by_slot[spec.slot]
        participants.append(participant)
        user = snapshot.users_by_id[participant.user_id]
        canonical_user = snapshot.canonical_users.get(spec.username)

        if user.username != spec.username:
            if canonical_user is not None and canonical_user.pk != user.pk:
                issues.append(
                    DriftIssue(
                        code=f"slot_{spec.slot}_username_conflict",
                        reconcilable=False,
                        message=(
                            f"슬롯 {spec.slot}의 예약 사용자 이름이 다른 사용자에게 "
                            "연결되어 있습니다."
                        ),
                    )
                )
            else:
                issues.append(
                    DriftIssue(
                        code=f"slot_{spec.slot}_username",
                        reconcilable=True,
                        message=f"슬롯 {spec.slot}의 사용자 이름이 다릅니다.",
                    )
                )
        if user.first_name != spec.display_name:
            issues.append(
                DriftIssue(
                    code=f"slot_{spec.slot}_first_name",
                    reconcilable=True,
                    message=f"슬롯 {spec.slot}의 로그인 이름이 다릅니다.",
                )
            )
        if not user.is_active or user.is_staff or user.is_superuser:
            issues.append(
                DriftIssue(
                    code=f"slot_{spec.slot}_permissions",
                    reconcilable=True,
                    message=f"슬롯 {spec.slot}의 사용자 권한 설정이 다릅니다.",
                )
            )
        password_status = _password_status(user.password, spec.pin)
        if password_status == PasswordStatus.MISMATCH:
            issues.append(
                DriftIssue(
                    code=f"slot_{spec.slot}_pin",
                    reconcilable=True,
                    message=f"슬롯 {spec.slot}의 PIN 설정이 다릅니다.",
                )
            )
        elif password_status == PasswordStatus.OUTDATED_HASH:
            issues.append(
                DriftIssue(
                    code=f"slot_{spec.slot}_pin_hash",
                    reconcilable=True,
                    message=f"슬롯 {spec.slot}의 PIN 해시 정책이 오래되었습니다.",
                )
            )
        if participant.display_name != spec.display_name:
            issues.append(
                DriftIssue(
                    code=f"slot_{spec.slot}_display_name",
                    reconcilable=True,
                    message=f"슬롯 {spec.slot}의 표시 이름이 다릅니다.",
                )
            )

    expected_target_by_source = {
        participants[0].pk: participants[1].pk,
        participants[1].pk: participants[0].pk,
    }
    for source_id, target_id in expected_target_by_source.items():
        score = snapshot.scores_by_source_id.get(source_id)
        source_slot = next(
            participant.slot
            for participant in participants
            if participant.pk == source_id
        )
        if score is None:
            issues.append(
                DriftIssue(
                    code=f"slot_{source_slot}_relationship_missing",
                    reconcilable=True,
                    message=f"슬롯 {source_slot}의 관계 점수가 없습니다.",
                )
            )
        elif score.target_participant_id != target_id:
            issues.append(
                DriftIssue(
                    code=f"slot_{source_slot}_relationship_target",
                    reconcilable=False,
                    message=f"슬롯 {source_slot}의 관계 방향이 다릅니다.",
                )
            )

    if snapshot.score_count > len(expected_target_by_source):
        issues.append(
            DriftIssue(
                code="unexpected_relationships",
                reconcilable=False,
                message="예상하지 않은 관계 점수가 있습니다.",
            )
        )

    if not issues:
        return Inspection(ProvisioningState.EXACT, ())
    if any(not issue.reconcilable for issue in issues):
        return Inspection(ProvisioningState.UNSAFE_DRIFT, tuple(issues))
    return Inspection(ProvisioningState.DRIFT, tuple(issues))


def _password_status(encoded_password, pin):
    if not check_password(pin, encoded_password, setter=None):
        return PasswordStatus.MISMATCH
    try:
        current_hasher = identify_hasher(encoded_password)
        preferred_hasher = get_hasher()
    except ValueError:
        return PasswordStatus.MISMATCH
    is_current = (
        current_hasher.algorithm == preferred_hasher.algorithm
        and not preferred_hasher.must_update(encoded_password)
    )
    if not is_current:
        return PasswordStatus.OUTDATED_HASH
    return PasswordStatus.EXACT


def _bootstrap(specifications):
    user_model = get_user_model()
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


def _reconcile(specifications, snapshot):
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

        if _password_status(user.password, spec.pin) != PasswordStatus.EXACT:
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


def _inspection_error_message(inspection):
    if inspection.state == ProvisioningState.EMPTY:
        return "참가자 설정이 비어 있습니다. 최초 설정 명령을 실행해 주세요."
    details = " ".join(issue.message for issue in inspection.issues)
    if inspection.state == ProvisioningState.UNSAFE_DRIFT:
        return f"안전하게 자동 복구할 수 없는 참가자 설정입니다. {details}"
    return f"참가자 설정 차이를 발견했습니다. 변경하지 않았습니다. {details}"
