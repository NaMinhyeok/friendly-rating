import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import ScoreChange

pytestmark = pytest.mark.django_db


def _create_score_change(participant_pair):
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=5,
        reason="고마운 일이 있었어요",
        resulting_score=5,
    )


@pytest.mark.parametrize("current_score", (-1, 101), ids=("below-zero", "above-100"))
def test_relationship_score_is_constrained_to_zero_through_one_hundred(
    participant_pair,
    current_score,
):
    score = participant_pair.first_to_second
    score.current_score = current_score

    with pytest.raises(IntegrityError), transaction.atomic():
        score.save(update_fields=("current_score",))


def test_relationship_score_requires_different_participants(participant_pair):
    participant_pair.second_to_first.delete()
    score = participant_pair.first_to_second
    score.target_participant = participant_pair.first

    with pytest.raises(IntegrityError), transaction.atomic():
        score.save(update_fields=("target_participant",))


def test_participant_slot_is_constrained_to_a_known_slot(participant_pair):
    participant = participant_pair.first
    participant.slot = 3

    with pytest.raises(IntegrityError), transaction.atomic():
        participant.save(update_fields=("slot",))


def test_score_change_delta_cannot_be_zero(participant_pair):
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreChange.objects.create(
            relationship_score=participant_pair.first_to_second,
            changed_by=participant_pair.first,
            delta=0,
            reason="변경 없음",
            resulting_score=0,
        )


@pytest.mark.parametrize(
    "resulting_score",
    (-1, 101),
    ids=("below-zero", "above-100"),
)
def test_score_change_result_is_constrained_to_zero_through_one_hundred(
    participant_pair,
    resulting_score,
):
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreChange.objects.create(
            relationship_score=participant_pair.first_to_second,
            changed_by=participant_pair.first,
            delta=1,
            reason="범위를 벗어난 결과",
            resulting_score=resulting_score,
        )


def test_existing_score_change_cannot_be_saved(participant_pair):
    change = _create_score_change(participant_pair)
    change.reason = "바꾼 이유"

    with pytest.raises(ValidationError):
        change.save()


def test_existing_score_change_cannot_be_deleted(participant_pair):
    change = _create_score_change(participant_pair)

    with pytest.raises(ValidationError):
        change.delete()


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_score_changes_cannot_be_changed_in_bulk(participant_pair, operation):
    change = _create_score_change(participant_pair)
    queryset = ScoreChange.objects.filter(pk=change.pk)

    with pytest.raises(ValidationError):
        if operation == "update":
            queryset.update(reason="바꾼 이유")
        else:
            queryset.delete()
