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


def test_dashboard_is_an_api_backed_shell(client, participant_pair):
    participant_pair.first_to_second.current_score = 12
    participant_pair.first_to_second.save(update_fields=("current_score",))
    participant_pair.second_to_first.current_score = 34
    participant_pair.second_to_first.save(update_fields=("current_score",))
    _login(client, participant_pair.first)

    response = client.get(reverse("home"))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-scores-url="/api/v1/relationship-scores/"' in content
    assert 'data-score-changes-url="/api/v1/score-changes/"' in content
    assert "ratings/dashboard.js" in content
    assert "점수를 어떻게 바꿀까요?" in content
    assert 'name="operation" value="increase" checked' in content
    assert 'name="operation" value="decrease"' in content
    assert 'name="operation" value="target"' in content
    assert "data-score-amount-label" in content
    assert "data-score-amount-hint" in content
    assert 'aria-describedby="id_amount_help id_score_preview"' in content
    assert "data-score-preview" in content
    assert 'aria-live="polite"' in content
    assert 'aria-atomic="true"' in content
    assert "<noscript>" in content
    assert "JavaScript를 켜 주세요" in content
    assert 'action="/score/change/"' not in content
    assert "첫 번째 → 두 번째" not in content
    assert "두 번째 → 첫 번째" not in content
    assert "12점" not in content
    assert "34점" not in content


def test_history_is_an_api_backed_shell(client, participant_pair):
    _create_change(participant_pair, 1)
    _login(client, participant_pair.second)

    response = client.get(reverse("history"), {"pageNumber": 2})

    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-history-url="/api/v1/score-changes/"' in content
    assert "ratings/history.js" in content
    assert "data-history-list" in content
    assert "data-history-pagination" in content
    assert "<noscript>" in content
    assert "JavaScript를 켜 주세요" in content
    assert "첫 번째 → 두 번째" not in content
    assert "+1점" not in content
    assert "변경자 첫 번째" not in content
    assert "변경 이유 1" not in content
    assert "변경 후 1점" not in content


def test_history_requires_login(client):
    response = client.get(reverse("history"))

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={reverse('history')}"


def test_score_change_thread_is_a_fresh_api_backed_shell(client, participant_pair):
    change = _create_change(participant_pair, 1, reason="화면에 미리 넣지 않을 이유")
    _login(client, participant_pair.second)

    response = client.get(
        reverse(
            "score-change-thread",
            kwargs={"score_change_id": change.pk},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert f'data-thread-url="/api/v1/score-changes/{change.pk}/"' in content
    assert f'data-comments-url="/api/v1/score-changes/{change.pk}/comments/"' in content
    assert "ratings/score_change_thread.js" in content
    assert 'name="csrfmiddlewaretoken"' in content
    assert "data-comment-list" in content
    assert "화면에 미리 넣지 않을 이유" not in content


def test_score_change_thread_requires_login_and_preserves_destination(
    client,
    participant_pair,
):
    change = _create_change(participant_pair, 1)
    thread_url = reverse(
        "score-change-thread",
        kwargs={"score_change_id": change.pk},
    )

    response = client.get(thread_url)

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={thread_url}"


def test_score_change_thread_returns_not_found_for_missing_change(
    client,
    participant_pair,
):
    _login(client, participant_pair.first)

    response = client.get(
        reverse("score-change-thread", kwargs={"score_change_id": 999999})
    )

    assert response.status_code == 404
