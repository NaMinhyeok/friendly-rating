import json
from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import resolve, reverse
from django.utils.dateparse import parse_datetime

from ..models import ScoreChange
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db


def _participant_client(participant):
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    home_response = client.get(reverse("home"))
    assert home_response.status_code == 200
    csrf_token = csrf_token_from_form(home_response, reverse("change-score"))
    return client, csrf_token


def _login_form_client():
    client = Client(enforce_csrf_checks=True)
    login_response = client.get(reverse("login"))
    assert login_response.status_code == 200
    csrf_token = csrf_token_from_form(login_response, None)
    return client, csrf_token


def _post_json(
    client,
    payload: object,
    *,
    csrf_token: str | None,
    origin: str = "http://testserver",
    accept: str = "application/json",
):
    headers = {
        "HTTP_ACCEPT": accept,
        "HTTP_ORIGIN": origin,
    }
    if csrf_token is not None:
        headers["HTTP_X_CSRFTOKEN"] = csrf_token
    return client.post(
        reverse("api-v1:score-change-list"),
        data=json.dumps(payload, ensure_ascii=False),
        content_type="application/json",
        **headers,
    )


def _assert_error_response(
    response,
    *,
    status_code: int,
    error_type: str,
    error_code: str,
) -> Mapping[str, object]:
    assert response.status_code == status_code
    assert response.headers["Content-Type"] == "application/json"

    body = response.json()
    assert set(body) == {"resultType", "error", "success"}
    assert body["resultType"] == "ERROR"
    assert body["success"] is None

    error = body["error"]
    assert isinstance(error, dict)
    assert set(error) == {"errorType", "errorCode", "reason", "details"}
    assert error["errorType"] == error_type
    assert error["errorCode"] == error_code
    assert isinstance(error["reason"], str)
    assert error["reason"]
    assert isinstance(error["details"], list)
    for detail in error["details"]:
        assert isinstance(detail, dict)
        assert set(detail) == {"field", "code", "message"}
        assert detail["field"] is None or isinstance(detail["field"], str)
        assert isinstance(detail["code"], str)
        assert isinstance(detail["message"], str)
    return body


def _assert_no_score_writes(participant_pair) -> None:
    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert participant_pair.second_to_first.current_score == 0
    assert not ScoreChange.objects.exists()


def test_score_change_api_url_name_and_path_are_stable():
    path = reverse("api-v1:score-change-list")

    assert path == "/api/v1/score-changes/"
    assert resolve(path).url_name == "score-change-list"
    assert resolve(path).namespace == "api-v1"


def test_participant_can_change_their_score_with_rendered_csrf_and_same_origin(
    participant_pair,
):
    participant_pair.first_to_second.current_score = 10
    participant_pair.first_to_second.save(update_fields=("current_score",))
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        {"delta": 3, "reason": "  고마워  "},
        csrf_token=csrf_token,
    )

    participant_pair.first_to_second.refresh_from_db()
    change = ScoreChange.objects.get()
    assert response.status_code == 201
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": change.pk,
            "delta": 3,
            "reason": "고마워",
            "resultingScore": 13,
            "createdAt": response.json()["success"]["createdAt"],
        },
    }
    assert parse_datetime(response.json()["success"]["createdAt"]) == change.created_at
    assert participant_pair.first_to_second.current_score == 13
    assert change.relationship_score == participant_pair.first_to_second
    assert change.changed_by == participant_pair.first
    assert change.reason == "고마워"


def test_second_participant_changes_only_their_outgoing_score(participant_pair):
    participant_pair.first_to_second.current_score = 20
    participant_pair.first_to_second.save(update_fields=("current_score",))
    participant_pair.second_to_first.current_score = 10
    participant_pair.second_to_first.save(update_fields=("current_score",))
    client, csrf_token = _participant_client(participant_pair.second)

    response = _post_json(
        client,
        {"delta": -3},
        csrf_token=csrf_token,
    )

    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    change = ScoreChange.objects.get()
    assert response.status_code == 201
    assert participant_pair.first_to_second.current_score == 20
    assert participant_pair.second_to_first.current_score == 7
    assert change.relationship_score == participant_pair.second_to_first
    assert change.changed_by == participant_pair.second
    assert change.delta == -3
    assert change.reason == ""
    assert change.resulting_score == 7
    assert response.json()["success"]["reason"] == ""


def test_anonymous_request_returns_json_authentication_error_without_writing(
    participant_pair,
):
    client, csrf_token = _login_form_client()

    response = _post_json(client, {"delta": 1}, csrf_token=csrf_token)

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )
    assert "Location" not in response.headers
    _assert_no_score_writes(participant_pair)


def test_authenticated_request_requires_csrf_without_writing(participant_pair):
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = _post_json(client, {"delta": 1}, csrf_token=None)

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    _assert_no_score_writes(participant_pair)


def test_authenticated_request_rejects_hostile_origin_with_valid_csrf(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        {"delta": 1},
        csrf_token=csrf_token,
        origin="https://attacker.example",
    )

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    _assert_no_score_writes(participant_pair)


def test_authenticated_non_participant_is_forbidden_without_writing(
    participant_pair,
):
    client, csrf_token = _login_form_client()
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="api-non-participant")
    client.force_login(user)

    response = _post_json(client, {"delta": 1}, csrf_token=csrf_token)

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )
    _assert_no_score_writes(participant_pair)


def test_score_change_api_rejects_non_json_media_type_without_writing(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = client.post(
        reverse("api-v1:score-change-list"),
        data="delta=1",
        content_type="text/plain",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=415,
        error_type="REQUEST",
        error_code="UNSUPPORTED_MEDIA_TYPE",
    )
    _assert_no_score_writes(participant_pair)


def test_score_change_api_rejects_malformed_json_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)

    response = client.post(
        reverse("api-v1:score-change-list"),
        data='{"delta":',
        content_type="application/json",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=400,
        error_type="REQUEST",
        error_code="INVALID_JSON",
    )
    _assert_no_score_writes(participant_pair)


def test_score_change_api_requires_post_without_writing(participant_pair):
    client, _csrf_token = _participant_client(participant_pair.first)

    response = client.get(
        reverse("api-v1:score-change-list"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error_response(
        response,
        status_code=405,
        error_type="REQUEST",
        error_code="METHOD_NOT_ALLOWED",
    )
    assert "POST" in response.headers["Allow"]
    _assert_no_score_writes(participant_pair)


def test_score_change_api_only_renders_json_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        {"delta": 1},
        csrf_token=csrf_token,
        accept="text/html",
    )

    _assert_error_response(
        response,
        status_code=406,
        error_type="REQUEST",
        error_code="NOT_ACCEPTABLE",
    )
    _assert_no_score_writes(participant_pair)


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        ({}, "delta", "REQUIRED"),
        ({"delta": None}, "delta", "INVALID_TYPE"),
        ({"delta": 0}, "delta", "NON_ZERO"),
        ({"delta": 101}, "delta", "MAX_VALUE"),
        ({"delta": -101}, "delta", "MIN_VALUE"),
        ({"delta": 1, "reason": "가" * 201}, "reason", "MAX_LENGTH"),
        ({"delta": True}, "delta", "INVALID_TYPE"),
        ({"delta": "1"}, "delta", "INVALID_TYPE"),
        ({"delta": 1.5}, "delta", "INVALID_TYPE"),
        ({"delta": 1, "reason": None}, "reason", "INVALID_TYPE"),
        ({"delta": 1, "reason": 7}, "reason", "INVALID_TYPE"),
        ([{"delta": 1}], None, "INVALID_TYPE"),
        ({"delta": 1, "unexpected": True}, "unexpected", "UNKNOWN_FIELD"),
    ),
)
def test_score_change_api_strictly_validates_json_without_writing(
    participant_pair,
    payload,
    expected_field,
    expected_code,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(client, payload, csrf_token=csrf_token)

    body = _assert_error_response(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    error = body["error"]
    assert isinstance(error, dict)
    assert any(
        detail["field"] == expected_field and detail["code"] == expected_code
        for detail in error["details"]
    )
    _assert_no_score_writes(participant_pair)


def test_score_change_api_returns_conflict_for_result_outside_score_range(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(client, {"delta": -1}, csrf_token=csrf_token)

    _assert_error_response(
        response,
        status_code=409,
        error_type="CONFLICT",
        error_code="SCORE_OUT_OF_RANGE",
    )
    _assert_no_score_writes(participant_pair)


def test_score_change_api_rejects_request_body_over_four_kibibytes_without_writing(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)
    request_body = json.dumps(
        {"delta": 1, "reason": "가" * 4096},
        ensure_ascii=False,
    ).encode()
    assert len(request_body) > 4096

    response = client.post(
        reverse("api-v1:score-change-list"),
        data=request_body,
        content_type="application/json",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=413,
        error_type="REQUEST",
        error_code="REQUEST_BODY_TOO_LARGE",
    )
    _assert_no_score_writes(participant_pair)


@override_settings(DEBUG=False)
def test_unexpected_api_error_is_generic_and_does_not_write(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.api.views.change_relationship_score",
        side_effect=RuntimeError("sensitive internal detail"),
    ):
        response = _post_json(client, {"delta": 1}, csrf_token=csrf_token)

    _assert_error_response(
        response,
        status_code=500,
        error_type="SERVER",
        error_code="INTERNAL_SERVER_ERROR",
    )
    assert b"sensitive internal detail" not in response.content
    _assert_no_score_writes(participant_pair)
