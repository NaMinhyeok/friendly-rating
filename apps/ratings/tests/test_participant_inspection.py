from types import SimpleNamespace

import pytest

from ..participant_provisioning import inspection
from ..participant_provisioning.contracts import (
    ParticipantSpec,
    PasswordStatus,
    ProvisioningState,
)
from ..participant_provisioning.snapshot import ProvisioningSnapshot


@pytest.fixture(autouse=True)
def current_passwords(monkeypatch):
    monkeypatch.setattr(
        inspection,
        "get_password_status",
        lambda *_: PasswordStatus.EXACT,
    )


@pytest.fixture
def specifications():
    return tuple(
        ParticipantSpec(
            slot=slot,
            username=f"participant-{slot}",
            display_name=display_name,
            pin=pin,
        )
        for slot, display_name, pin in (
            (1, "민수", "1234"),
            (2, "지수", "5678"),
        )
    )


@pytest.fixture
def complete_snapshot(specifications):
    users = {
        spec.slot: SimpleNamespace(
            pk=spec.slot * 10,
            username=spec.username,
            first_name=spec.display_name,
            password="unused-by-this-test",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        for spec in specifications
    }
    participants = {
        spec.slot: SimpleNamespace(
            pk=spec.slot * 100,
            user_id=users[spec.slot].pk,
            display_name=spec.display_name,
            slot=spec.slot,
        )
        for spec in specifications
    }
    first, second = participants.values()

    return ProvisioningSnapshot(
        participants_by_slot=participants,
        users_by_id={user.pk: user for user in users.values()},
        canonical_users={user.username: user for user in users.values()},
        scores_by_source_id={
            first.pk: SimpleNamespace(target_participant_id=second.pk),
            second.pk: SimpleNamespace(target_participant_id=first.pk),
        },
        participant_count=2,
        score_count=2,
    )


def test_repairable_difference_is_classified_as_drift(
    specifications,
    complete_snapshot,
):
    first = complete_snapshot.participants_by_slot[1]
    complete_snapshot.users_by_id[first.user_id].is_staff = True

    result = inspection.inspect_snapshot(specifications, complete_snapshot)

    assert result.state is ProvisioningState.DRIFT
    assert result.issues
    assert all(issue.reconcilable for issue in result.issues)


def test_incomplete_graph_is_classified_as_unsafe_drift(
    specifications,
    complete_snapshot,
):
    complete_snapshot.participants_by_slot.pop(2)
    complete_snapshot.participant_count = 1

    result = inspection.inspect_snapshot(specifications, complete_snapshot)

    assert result.state is ProvisioningState.UNSAFE_DRIFT
    assert result.issues
    assert any(not issue.reconcilable for issue in result.issues)
