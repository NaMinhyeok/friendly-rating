import pytest
from django.urls import reverse

from ..models import ScoreChange

pytestmark = pytest.mark.django_db


def _login(client, participant):
    client.force_login(participant.user)


def _create_change(participant_pair, number, *, reason=None):
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=1,
        reason=f"변경 이유 {number}" if reason is None else reason,
        resulting_score=1,
    )


def test_dashboard_shows_both_directional_scores(client, participant_pair):
    participant_pair.first_to_second.current_score = 12
    participant_pair.first_to_second.save(update_fields=("current_score",))
    participant_pair.second_to_first.current_score = 34
    participant_pair.second_to_first.save(update_fields=("current_score",))
    _login(client, participant_pair.first)

    response = client.get(reverse("home"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "첫 번째 → 두 번째" in content
    assert "두 번째 → 첫 번째" in content
    assert "12점" in content
    assert "34점" in content


@pytest.mark.parametrize(
    ("operation", "reason", "expected_delta", "expected_score"),
    (
        ("increase", None, 3, 13),
        ("decrease", "대표 변경", -3, 7),
    ),
)
def test_score_operation_maps_to_the_expected_delta(
    client,
    participant_pair,
    operation,
    reason,
    expected_delta,
    expected_score,
):
    participant_pair.first_to_second.current_score = 10
    participant_pair.first_to_second.save(update_fields=("current_score",))
    _login(client, participant_pair.first)

    payload = {"operation": operation, "amount": 3}
    if reason is not None:
        payload["reason"] = reason
    response = client.post(reverse("change-score"), payload)

    participant_pair.first_to_second.refresh_from_db()
    change = ScoreChange.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("home")
    assert participant_pair.first_to_second.current_score == expected_score
    assert change.delta == expected_delta
    assert change.reason == (reason or "")


def test_out_of_range_change_returns_bad_request_without_writing(
    client,
    participant_pair,
):
    _login(client, participant_pair.first)

    response = client.post(
        reverse("change-score"),
        {
            "operation": "decrease",
            "amount": 1,
            "reason": "범위 밖 변경",
        },
    )

    participant_pair.first_to_second.refresh_from_db()
    assert response.status_code == 400
    assert "0점보다 낮거나" in response.content.decode()
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()


def test_score_change_rejects_a_reason_over_two_hundred_characters(
    client,
    participant_pair,
):
    _login(client, participant_pair.first)

    response = client.post(
        reverse("change-score"),
        {"operation": "increase", "amount": 1, "reason": "가" * 201},
    )

    assert response.status_code == 400
    assert "200자" in response.content.decode()
    assert not ScoreChange.objects.exists()


def test_score_change_endpoint_requires_post(client, participant_pair):
    _login(client, participant_pair.first)

    response = client.get(reverse("change-score"))

    assert response.status_code == 405


def test_history_shows_complete_change_details(client, participant_pair):
    _create_change(participant_pair, 1)
    _login(client, participant_pair.second)

    response = client.get(reverse("history"))

    content = response.content.decode()
    assert response.status_code == 200
    assert "첫 번째 → 두 번째" in content
    assert "+1점" in content
    assert "변경자 첫 번째" in content
    assert "변경 이유 1" in content
    assert "변경 후 1점" in content


def test_history_is_paginated_twenty_per_page(client, participant_pair):
    for number in range(21):
        _create_change(participant_pair, number)
    _login(client, participant_pair.second)

    first_page = client.get(reverse("history"))
    second_page = client.get(reverse("history"), {"page": 2})

    first_page_content = first_page.content.decode()
    second_page_content = second_page.content.decode()
    assert "변경 이유 20" in first_page_content
    assert "변경 이유 0" not in first_page_content
    assert "다음 →" in first_page_content
    assert "변경 이유 0" in second_page_content
    assert "변경 이유 20" not in second_page_content
    assert "← 이전" in second_page_content


def test_history_requires_login(client):
    response = client.get(reverse("history"))

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={reverse('history')}"
