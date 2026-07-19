import json
from collections.abc import Mapping
from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import resolve, reverse

from ..models import PushDevice
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db

VALID_FID = "c12345678901234567890A"
SECOND_FID = "d12345678901234567890B"


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
    url_name: str,
    payload: object,
    *,
    csrf_token: str | None,
    origin: str = "http://testserver",
):
    headers = {
        "HTTP_ACCEPT": "application/json",
        "HTTP_ORIGIN": origin,
    }
    if csrf_token is not None:
        headers["HTTP_X_CSRFTOKEN"] = csrf_token
    return client.post(
        reverse(f"api-v1:{url_name}"),
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _device_state():
    return list(
        PushDevice.objects.order_by("pk").values_list(
            "pk",
            "participant_id",
            "firebase_installation_id",
            "is_active",
            "user_agent",
            "created_at",
            "updated_at",
        )
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
    return body


@pytest.mark.parametrize(
    ("url_name", "path"),
    (
        ("push-device-register", "/api/v1/push-devices/register/"),
        ("push-device-unregister", "/api/v1/push-devices/unregister/"),
    ),
)
def test_push_device_api_url_names_and_paths_are_stable(url_name, path):
    resolved_path = reverse(f"api-v1:{url_name}")

    assert resolved_path == path
    assert resolve(resolved_path).url_name == url_name
    assert resolve(resolved_path).namespace == "api-v1"


def test_participant_registers_device_with_rendered_csrf_and_same_origin(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = client.post(
        reverse("api-v1:push-device-register"),
        data=json.dumps({"fid": VALID_FID}),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_USER_AGENT="test-browser/1.0",
    )

    device = PushDevice.objects.get(firebase_installation_id=VALID_FID)
    assert response.status_code == 200
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {"registered": True},
    }
    assert response.json()["success"]["registered"] is True
    assert device.participant == participant_pair.first
    assert device.is_active
    assert device.user_agent == "test-browser/1.0"
    assert VALID_FID not in response.content.decode()
    assert "test-browser" not in response.content.decode()


def test_registration_reactivates_and_reassigns_without_exposing_row_state(
    participant_pair,
):
    existing = PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
        is_active=False,
    )
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        "push-device-register",
        {"fid": VALID_FID},
        csrf_token=csrf_token,
    )

    existing.refresh_from_db()
    assert response.status_code == 200
    assert response.json()["success"] == {"registered": True}
    assert existing.participant == participant_pair.first
    assert existing.is_active


def test_registration_accepts_url_safe_fid_characters(participant_pair):
    fid = f"c{'A' * 19}-_"
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        "push-device-register",
        {"fid": fid},
        csrf_token=csrf_token,
    )

    assert response.status_code == 200
    assert PushDevice.objects.filter(
        participant=participant_pair.first,
        firebase_installation_id=fid,
        is_active=True,
    ).exists()


def test_registration_keeps_only_the_five_most_recent_devices(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)
    fids = [f"c{'A' * 20}{index}" for index in range(6)]

    for fid in fids:
        response = _post_json(
            client,
            "push-device-register",
            {"fid": fid},
            csrf_token=csrf_token,
        )
        assert response.status_code == 200

    active_devices = PushDevice.objects.filter(
        participant=participant_pair.first,
        is_active=True,
    )
    assert active_devices.count() == 5
    assert not active_devices.filter(firebase_installation_id=fids[0]).exists()
    assert set(
        active_devices.values_list("firebase_installation_id", flat=True)
    ) == set(fids[1:])


@pytest.mark.parametrize("device_exists", (False, True))
def test_unregister_is_idempotent_for_owned_device(participant_pair, device_exists):
    device = (
        PushDevice.objects.create(
            participant=participant_pair.first,
            firebase_installation_id=VALID_FID,
        )
        if device_exists
        else None
    )
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        "push-device-unregister",
        {"fid": VALID_FID},
        csrf_token=csrf_token,
    )

    assert response.status_code == 200
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {"registered": False},
    }
    assert response.json()["success"]["registered"] is False
    if device is not None:
        device.refresh_from_db()
        assert not device.is_active


def test_participant_cannot_unregister_another_participants_device(
    participant_pair,
):
    device = PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        "push-device-unregister",
        {"fid": VALID_FID},
        csrf_token=csrf_token,
    )

    device.refresh_from_db()
    assert response.status_code == 200
    assert response.json()["success"] == {"registered": False}
    assert device.is_active


@pytest.mark.parametrize(
    "url_name",
    ("push-device-register", "push-device-unregister"),
)
def test_anonymous_push_device_request_returns_json_error_without_writing(
    participant_pair,
    url_name,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client, csrf_token = _login_form_client()
    original_state = _device_state()

    response = _post_json(
        client,
        url_name,
        {"fid": VALID_FID},
        csrf_token=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )
    assert "Location" not in response.headers
    assert _device_state() == original_state


@pytest.mark.parametrize(
    ("csrf_token", "origin"),
    (
        (None, "http://testserver"),
        ("rendered", "https://attacker.example"),
    ),
)
def test_push_device_request_rejects_csrf_failures_without_writing(
    participant_pair,
    csrf_token,
    origin,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client, rendered_token = _participant_client(participant_pair.first)
    original_state = _device_state()
    token = rendered_token if csrf_token == "rendered" else None

    response = _post_json(
        client,
        "push-device-register",
        {"fid": SECOND_FID},
        csrf_token=token,
        origin=origin,
    )

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    assert _device_state() == original_state


def test_authenticated_non_participant_is_forbidden_without_writing(
    participant_pair,
):
    client, csrf_token = _login_form_client()
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="api-push-non-participant")
    client.force_login(user)
    original_state = _device_state()

    response = _post_json(
        client,
        "push-device-register",
        {"fid": VALID_FID},
        csrf_token=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )
    assert _device_state() == original_state


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        (None, None, "INVALID_TYPE"),
        ({}, "fid", "REQUIRED"),
        ({"fid": None}, "fid", "INVALID_TYPE"),
        ({"fid": 123}, "fid", "INVALID_TYPE"),
        ({"fid": f"c{'A' * 20}"}, "fid", "MIN_LENGTH"),
        ({"fid": f"c{'A' * 22}"}, "fid", "MAX_LENGTH"),
        ({"fid": f"a{'A' * 21}"}, "fid", "INVALID_FORMAT"),
        ({"fid": f"c{'A' * 20}/"}, "fid", "INVALID_FORMAT"),
        ({"fid": f" c{'A' * 21}"}, "fid", "MAX_LENGTH"),
        ({"fid": VALID_FID, "participantId": 1}, "participantId", "UNKNOWN_FIELD"),
    ),
)
def test_push_device_api_strictly_validates_input_without_writing(
    participant_pair,
    payload,
    expected_field,
    expected_code,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=SECOND_FID,
    )
    client, csrf_token = _participant_client(participant_pair.first)
    original_state = _device_state()

    response = _post_json(
        client,
        "push-device-register",
        payload,
        csrf_token=csrf_token,
    )

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
    assert _device_state() == original_state


def test_push_device_api_rejects_malformed_json_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)
    original_state = _device_state()

    response = client.post(
        reverse("api-v1:push-device-register"),
        data='{"fid":',
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
    assert _device_state() == original_state


def test_push_device_api_rejects_non_json_media_type_without_writing(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)
    original_state = _device_state()

    response = client.post(
        reverse("api-v1:push-device-register"),
        data=f"fid={VALID_FID}",
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
    assert _device_state() == original_state


def test_push_device_api_rejects_oversized_body_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)
    original_state = _device_state()

    response = _post_json(
        client,
        "push-device-register",
        {"fid": VALID_FID, "padding": "x" * 4096},
        csrf_token=csrf_token,
    )

    _assert_error_response(
        response,
        status_code=413,
        error_type="REQUEST",
        error_code="REQUEST_BODY_TOO_LARGE",
    )
    assert _device_state() == original_state


@pytest.mark.parametrize(
    "url_name",
    ("push-device-register", "push-device-unregister"),
)
def test_push_device_api_requires_post_without_writing(
    participant_pair,
    url_name,
):
    client, _csrf_token = _participant_client(participant_pair.first)
    original_state = _device_state()

    response = client.get(
        reverse(f"api-v1:{url_name}"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error_response(
        response,
        status_code=405,
        error_type="REQUEST",
        error_code="METHOD_NOT_ALLOWED",
    )
    assert _device_state() == original_state
