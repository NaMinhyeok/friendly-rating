from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import resolve, reverse
from django.utils.dateparse import parse_datetime

from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db


def test_relationship_score_api_url_name_and_path_are_stable():
    path = reverse("api-v1:relationship-score-list")

    assert path == "/api/v1/relationship-scores/"
    assert resolve(path).url_name == "relationship-score-list"
    assert resolve(path).namespace == "api-v1"


def test_participant_can_read_both_scores_with_their_score_first(
    participant_pair,
):
    participant_pair.first_to_second.current_score = 12
    participant_pair.first_to_second.save(update_fields=("current_score",))
    participant_pair.second_to_first.current_score = 34
    participant_pair.second_to_first.save(update_fields=("current_score",))
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(participant_pair.second.user)

    response = csrf_client.get(
        reverse("api-v1:relationship-score-list"),
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
                    "sourceParticipant": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "targetParticipant": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "currentScore": 34,
                    "updatedAt": body["success"]["results"][0]["updatedAt"],
                    "isMine": True,
                },
                {
                    "sourceParticipant": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "targetParticipant": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "currentScore": 12,
                    "updatedAt": body["success"]["results"][1]["updatedAt"],
                    "isMine": False,
                },
            ],
        },
    }
    assert parse_datetime(body["success"]["results"][0]["updatedAt"]) == (
        participant_pair.second_to_first.updated_at
    )
    assert parse_datetime(body["success"]["results"][1]["updatedAt"]) == (
        participant_pair.first_to_second.updated_at
    )
    assert "id" not in response.content.decode()
    assert "username" not in response.content.decode()


def test_anonymous_relationship_score_request_returns_json_authentication_error(
    client,
):
    response = client.get(
        reverse("api-v1:relationship-score-list"),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 403
    assert "Location" not in response.headers
    assert response.json() == {
        "resultType": "ERROR",
        "error": {
            "errorType": "AUTHENTICATION",
            "errorCode": "AUTHENTICATION_REQUIRED",
            "reason": "로그인이 필요합니다.",
            "details": [],
        },
        "success": None,
    }


def test_authenticated_non_participant_cannot_read_relationship_scores(
    client,
    participant_pair,
):
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="not-a-participant")
    client.force_login(user)

    response = client.get(
        reverse("api-v1:relationship-score-list"),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 403
    assert response.json()["error"]["errorCode"] == "PARTICIPANT_REQUIRED"


def test_relationship_score_api_rejects_non_json_accept_header(participant_pair):
    client = Client()
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse("api-v1:relationship-score-list"),
        HTTP_ACCEPT="text/html",
    )

    assert response.status_code == 406
    assert response.json()["error"]["errorCode"] == "NOT_ACCEPTABLE"


def test_relationship_score_api_rejects_mutation_with_json_method_error(
    participant_pair,
):
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)
    home_response = client.get(reverse("home"))
    csrf_token = csrf_token_from_form(home_response, reverse("logout"))

    response = client.post(
        reverse("api-v1:relationship-score-list"),
        data="{}",
        content_type="application/json",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 405
    assert response.json() == {
        "resultType": "ERROR",
        "error": {
            "errorType": "REQUEST",
            "errorCode": "METHOD_NOT_ALLOWED",
            "reason": "지원하지 않는 HTTP 메서드입니다.",
            "details": [],
        },
        "success": None,
    }
    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert participant_pair.second_to_first.current_score == 0
