from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from ..models import ScoreChange, ScoreChangeComment

pytestmark = pytest.mark.django_db


def _create_change(participant_pair, number, *, reverse_direction=False):
    relationship_score = (
        participant_pair.second_to_first
        if reverse_direction
        else participant_pair.first_to_second
    )
    changed_by = (
        participant_pair.second if reverse_direction else participant_pair.first
    )
    return ScoreChange.objects.create(
        relationship_score=relationship_score,
        changed_by=changed_by,
        delta=-1 if reverse_direction else 1,
        reason=f"변경 이유 {number}",
        resulting_score=number,
    )


def _assert_error(response, *, status_code, error_type, error_code):
    assert response.status_code == status_code
    assert response.headers["Content-Type"] == "application/json"
    body = response.json()
    assert body["resultType"] == "ERROR"
    assert body["success"] is None
    assert body["error"]["errorType"] == error_type
    assert body["error"]["errorCode"] == error_code
    return body["error"]


def test_participant_can_read_shared_bidirectional_history(participant_pair):
    first_change = _create_change(participant_pair, 1)
    second_change = _create_change(participant_pair, 2, reverse_direction=True)
    ScoreChangeComment.objects.create(
        score_change=first_change,
        author=participant_pair.second,
        content="첫 댓글",
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.second.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == "private, no-store"
    body = response.json()
    assert body == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "results": [
                {
                    "id": second_change.pk,
                    "sourceParticipant": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "targetParticipant": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "changedBy": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "delta": -1,
                    "reason": "변경 이유 2",
                    "resultingScore": 2,
                    "createdAt": body["success"]["results"][0]["createdAt"],
                    "commentCount": 0,
                    "threadUrl": reverse(
                        "score-change-thread",
                        kwargs={"score_change_id": second_change.pk},
                    ),
                    "attachments": [],
                },
                {
                    "id": first_change.pk,
                    "sourceParticipant": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "targetParticipant": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "changedBy": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "delta": 1,
                    "reason": "변경 이유 1",
                    "resultingScore": 1,
                    "createdAt": body["success"]["results"][1]["createdAt"],
                    "commentCount": 1,
                    "threadUrl": reverse(
                        "score-change-thread",
                        kwargs={"score_change_id": first_change.pk},
                    ),
                    "attachments": [],
                },
            ],
            "paging": {
                "pageNumber": 1,
                "pageSize": 20,
                "hasNext": False,
                "totalCount": 2,
            },
        },
    }
    assert parse_datetime(body["success"]["results"][0]["createdAt"]) == (
        second_change.created_at
    )
    assert parse_datetime(body["success"]["results"][1]["createdAt"]) == (
        first_change.created_at
    )


def test_history_uses_twenty_item_pages_in_stable_latest_first_order(
    client,
    participant_pair,
):
    for number in range(21):
        _create_change(participant_pair, number)
    client.force_login(participant_pair.first.user)

    first_page = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )
    second_page = client.get(
        reverse("api-v1:score-change-list"),
        {"pageNumber": 2},
        HTTP_ACCEPT="application/json",
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    first_success = first_page.json()["success"]
    second_success = second_page.json()["success"]
    assert [item["reason"] for item in first_success["results"]] == [
        f"변경 이유 {number}" for number in range(20, 0, -1)
    ]
    assert first_success["paging"] == {
        "pageNumber": 1,
        "pageSize": 20,
        "hasNext": True,
        "totalCount": 21,
    }
    assert [item["reason"] for item in second_success["results"]] == ["변경 이유 0"]
    assert second_success["paging"] == {
        "pageNumber": 2,
        "pageSize": 20,
        "hasNext": False,
        "totalCount": 21,
    }


def test_empty_history_uses_the_same_results_and_paging_shape(client, participant_pair):
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    assert response.json()["success"] == {
        "results": [],
        "paging": {
            "pageNumber": 1,
            "pageSize": 20,
            "hasNext": False,
            "totalCount": 0,
        },
    }


@pytest.mark.parametrize("page_number", ("", "0", "-1", "abc", "1.5"))
def test_history_rejects_invalid_page_numbers(client, participant_pair, page_number):
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        {"pageNumber": page_number},
        HTTP_ACCEPT="application/json",
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"][0]["field"] == "pageNumber"
    assert not ScoreChange.objects.exists()


def test_history_rejects_unknown_query_fields(client, participant_pair):
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        {"pageSize": 100},
        HTTP_ACCEPT="application/json",
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"][0]["field"] == "pageSize"


def test_history_returns_not_found_for_a_page_past_the_end(client, participant_pair):
    _create_change(participant_pair, 1)
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        {"pageNumber": 2},
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )
    assert ScoreChange.objects.count() == 1


def test_anonymous_history_request_returns_json_authentication_error(client):
    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )
    assert "Location" not in response.headers


def test_authenticated_non_participant_cannot_read_history(client, participant_pair):
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="not-a-participant")
    client.force_login(user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )


def test_history_rejects_non_json_accept_header(client, participant_pair):
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="text/html",
    )

    _assert_error(
        response,
        status_code=406,
        error_type="REQUEST",
        error_code="NOT_ACCEPTABLE",
    )
