import pytest
from django.core.exceptions import ValidationError

from ..score_rules import (
    ScoreUnchangedError,
    calculate_resulting_score,
    prepare_score_change,
    prepare_target_score_change,
)


@pytest.mark.parametrize(
    ("current_score", "delta", "expected_score"),
    (
        (1, -1, 0),
        (99, 1, 100),
    ),
    ids=("lower-boundary", "upper-boundary"),
)
def test_calculates_scores_at_both_boundaries(
    current_score,
    delta,
    expected_score,
):
    change = prepare_score_change(delta=delta)

    resulting_score = calculate_resulting_score(
        current_score=current_score,
        change=change,
    )

    assert resulting_score == expected_score
    assert change.delta == delta


@pytest.mark.parametrize(
    ("current_score", "delta"),
    (
        (0, -1),
        (100, 1),
    ),
    ids=("below-minimum", "above-maximum"),
)
def test_rejects_scores_outside_the_allowed_range(current_score, delta):
    change = prepare_score_change(delta=delta)

    with pytest.raises(ValidationError) as raised:
        calculate_resulting_score(
            current_score=current_score,
            change=change,
        )

    assert raised.value.messages == [
        "친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다."
    ]


@pytest.mark.parametrize(
    "delta",
    (0, True, 1.0),
    ids=("zero", "boolean", "non-integer"),
)
def test_rejects_zero_boolean_and_non_integer_deltas(delta):
    with pytest.raises(ValidationError) as raised:
        prepare_score_change(delta=delta)

    assert raised.value.messages == ["변경 점수는 0이 아닌 정수여야 합니다."]


@pytest.mark.parametrize(
    ("reason", "expected_reason"),
    (
        ("  오늘  정말 고마웠어요\n", "오늘  정말 고마웠어요"),
        (" \t\n ", ""),
    ),
    ids=("surrounding-whitespace", "whitespace-only"),
)
def test_normalizes_reason_whitespace(reason, expected_reason):
    change = prepare_score_change(delta=1, reason=reason)

    assert change.reason == expected_reason


def test_defaults_reason_to_empty():
    change = prepare_score_change(delta=1)

    assert change.reason == ""


def test_accepts_two_hundred_reason_characters_after_normalizing():
    change = prepare_score_change(
        delta=1,
        reason=f" {'가' * 200} ",
    )

    assert change.reason == "가" * 200


def test_rejects_more_than_two_hundred_reason_characters():
    with pytest.raises(ValidationError) as raised:
        prepare_score_change(
            delta=1,
            reason="가" * 201,
        )

    assert raised.value.messages == ["변경 이유는 200자 이하여야 합니다."]


def test_rejects_a_non_string_reason():
    with pytest.raises(ValidationError) as raised:
        prepare_score_change(
            delta=1,
            reason=None,  # pyrefly: ignore[bad-argument-type]
        )

    assert raised.value.messages == ["변경 이유는 문자열이어야 합니다."]


@pytest.mark.parametrize(
    ("current_score", "target_score", "expected_delta"),
    (
        (1, 0, -1),
        (35, 100, 65),
    ),
    ids=("lower-boundary", "upper-boundary"),
)
def test_prepares_an_absolute_target_as_a_delta(
    current_score,
    target_score,
    expected_delta,
):
    change = prepare_target_score_change(
        current_score=current_score,
        target_score=target_score,
        reason="  최종 점수로 기록  ",
    )

    assert change.delta == expected_delta
    assert change.reason == "최종 점수로 기록"


@pytest.mark.parametrize(
    "target_score",
    (True, 1.0, "1"),
    ids=("boolean", "float", "string"),
)
def test_rejects_non_integer_absolute_targets(target_score):
    with pytest.raises(ValidationError) as raised:
        prepare_target_score_change(
            current_score=0,
            target_score=target_score,
        )

    assert raised.value.messages == ["최종 점수는 정수여야 합니다."]


@pytest.mark.parametrize("target_score", (-1, 101), ids=("below-zero", "above-100"))
def test_rejects_absolute_targets_outside_the_allowed_range(target_score):
    with pytest.raises(ValidationError) as raised:
        prepare_target_score_change(
            current_score=50,
            target_score=target_score,
        )

    assert raised.value.messages == ["최종 점수는 0점 이상 100점 이하여야 합니다."]


def test_rejects_an_absolute_target_matching_the_current_score():
    with pytest.raises(ScoreUnchangedError, match="현재 점수와 같습니다"):
        prepare_target_score_change(current_score=50, target_score=50)
