from django.contrib.auth.hashers import check_password, get_hasher, identify_hasher

from .contracts import (
    DriftIssue,
    Inspection,
    PasswordStatus,
    ProvisioningState,
)


def inspect_snapshot(specifications, snapshot):
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
        password_status = get_password_status(user.password, spec.pin)
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


def get_password_status(encoded_password, pin):
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


def inspection_error_message(inspection):
    if inspection.state == ProvisioningState.EMPTY:
        return "참가자 설정이 비어 있습니다. 최초 설정 명령을 실행해 주세요."
    details = " ".join(issue.message for issue in inspection.issues)
    if inspection.state == ProvisioningState.UNSAFE_DRIFT:
        return f"안전하게 자동 복구할 수 없는 참가자 설정입니다. {details}"
    return f"참가자 설정 차이를 발견했습니다. 변경하지 않았습니다. {details}"
