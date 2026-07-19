import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from firebase_admin import messaging

from ..models import PushDevice
from ..notifications import send_score_change_notification
from ..services import change_relationship_score

VALID_FID = "c12345678901234567890A"
SECOND_FID = "d12345678901234567890B"
SENDER_FID = "e12345678901234567890C"
TEST_PUBLIC_BASE_URL = "https://friendly-rating.example.test/"


@pytest.fixture
def push_delivery_settings(settings):
    settings.PUSH_NOTIFICATIONS_ENABLED = True
    settings.PUBLIC_BASE_URL = TEST_PUBLIC_BASE_URL


def _post_json(client, url_name, fid=VALID_FID):
    return client.post(
        reverse(url_name),
        data=json.dumps({"fid": fid}),
        content_type="application/json",
    )


def _push_device_state():
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


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.django_db
def test_push_device_mutations_require_login_without_writing(client, url_name):
    response = _post_json(client, url_name)

    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"{reverse('login')}?next={reverse(url_name)}"
    )
    assert not PushDevice.objects.exists()


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.django_db
def test_push_device_mutations_require_csrf_without_writing(
    participant_pair,
    url_name,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(participant_pair.first.user)
    original_state = _push_device_state()

    response = _post_json(csrf_client, url_name)

    assert response.status_code == 403
    assert _push_device_state() == original_state


@pytest.mark.parametrize(
    ("url_name", "expected_status", "expected_registered"),
    (
        ("register-push-device", 201, True),
        ("unregister-push-device", 200, False),
    ),
)
@pytest.mark.django_db
def test_push_device_mutations_accept_a_valid_csrf_token(
    participant_pair,
    url_name,
    expected_status,
    expected_registered,
):
    if url_name == "unregister-push-device":
        PushDevice.objects.create(
            participant=participant_pair.first,
            firebase_installation_id=VALID_FID,
        )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(participant_pair.first.user)
    home_response = csrf_client.get(reverse("home"))
    csrf_token = csrf_client.cookies["csrftoken"].value

    response = csrf_client.post(
        reverse(url_name),
        data=json.dumps({"fid": VALID_FID}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    device = PushDevice.objects.get(firebase_installation_id=VALID_FID)
    assert home_response.status_code == 200
    assert response.status_code == expected_status
    assert response.json() == {
        "ok": True,
        "registered": expected_registered,
    }
    assert device.participant == participant_pair.first
    assert device.is_active is expected_registered


@pytest.mark.django_db
def test_participant_can_register_multiple_devices(client, participant_pair):
    client.force_login(participant_pair.first.user)

    first_response = _post_json(client, "register-push-device")
    second_response = _post_json(client, "register-push-device", SECOND_FID)

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.headers["Content-Type"] == "application/json"
    assert second_response.headers["Content-Type"] == "application/json"
    assert first_response.json() == {"ok": True, "registered": True}
    assert second_response.json() == {"ok": True, "registered": True}
    assert participant_pair.first.push_devices.filter(is_active=True).count() == 2


@pytest.mark.django_db
def test_registration_reactivates_and_reassigns_a_fid(client, participant_pair):
    PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
        is_active=False,
    )
    client.force_login(participant_pair.first.user)

    response = _post_json(client, "register-push-device")

    device = PushDevice.objects.get(firebase_installation_id=VALID_FID)
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"ok": True, "registered": True}
    assert device.participant == participant_pair.first
    assert device.is_active


@pytest.mark.django_db
def test_registration_keeps_only_the_five_most_recent_devices(
    client,
    participant_pair,
):
    client.force_login(participant_pair.first.user)

    for index in range(6):
        response = _post_json(
            client,
            "register-push-device",
            f"c{'A' * 20}{index}",
        )
        assert response.status_code == 201

    devices = participant_pair.first.push_devices.order_by("updated_at")
    assert devices.count() == 5
    assert not devices.filter(firebase_installation_id=f"c{'A' * 20}0").exists()


@pytest.mark.django_db
def test_participant_cannot_unregister_another_participants_device(
    client,
    participant_pair,
):
    device = PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    client.force_login(participant_pair.first.user)

    response = _post_json(client, "unregister-push-device")

    device.refresh_from_db()
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"ok": True, "registered": False}
    assert device.is_active


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.parametrize(
    "fid",
    (
        pytest.param(None, id="not-a-string-null"),
        pytest.param(123, id="not-a-string-number"),
        pytest.param("not valid", id="not-url-safe"),
        pytest.param("a12345678901234567890A", id="invalid-prefix"),
        pytest.param(f"c{'A' * 20}", id="too-short"),
        pytest.param(f"c{'A' * 22}", id="too-long"),
    ),
)
@pytest.mark.django_db
def test_invalid_fid_is_rejected_without_writing(
    client,
    participant_pair,
    url_name,
    fid,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client.force_login(participant_pair.first.user)
    original_state = _push_device_state()

    response = _post_json(client, url_name, fid)

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": "올바른 Firebase 기기 ID가 필요합니다.",
    }
    assert _push_device_state() == original_state


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.parametrize(
    ("body", "content_type", "expected_status", "expected_error"),
    (
        pytest.param(
            f"fid={VALID_FID}",
            "text/plain",
            415,
            "application/json 요청만 지원합니다.",
            id="non-json-content-type",
        ),
        pytest.param(
            "{",
            "application/json",
            400,
            "올바른 JSON을 입력해 주세요.",
            id="malformed-json",
        ),
        pytest.param(
            "[]",
            "application/json",
            400,
            "올바른 Firebase 기기 ID가 필요합니다.",
            id="non-object-json",
        ),
        pytest.param(
            "{}",
            "application/json",
            400,
            "올바른 Firebase 기기 ID가 필요합니다.",
            id="missing-fid",
        ),
        pytest.param(
            json.dumps({"fid": VALID_FID, "padding": "x" * 4096}),
            "application/json",
            400,
            "요청이 너무 큽니다.",
            id="body-over-4096-bytes",
        ),
    ),
)
@pytest.mark.django_db
def test_push_json_boundaries_reject_without_writing(
    client,
    participant_pair,
    url_name,
    body,
    content_type,
    expected_status,
    expected_error,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client.force_login(participant_pair.first.user)
    original_state = _push_device_state()

    response = client.post(
        reverse(url_name),
        data=body,
        content_type=content_type,
    )

    assert response.status_code == expected_status
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"ok": False, "error": expected_error}
    assert _push_device_state() == original_state


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.django_db
def test_push_device_mutations_require_post_without_writing(
    client,
    participant_pair,
    url_name,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client.force_login(participant_pair.first.user)
    original_state = _push_device_state()

    response = client.get(reverse(url_name))

    assert response.status_code == 405
    assert _push_device_state() == original_state


@pytest.mark.parametrize(
    "url_name",
    ("register-push-device", "unregister-push-device"),
)
@pytest.mark.django_db
def test_authenticated_non_participant_cannot_mutate_push_devices(
    client,
    participant_pair,
    url_name,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="not-a-participant")
    client.force_login(user)
    original_state = _push_device_state()

    response = _post_json(client, url_name)

    assert response.status_code == 403
    assert _push_device_state() == original_state


@pytest.mark.django_db
def test_owned_device_can_be_unregistered(client, participant_pair):
    device = PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    client.force_login(participant_pair.first.user)

    response = _post_json(client, "unregister-push-device")

    device.refresh_from_db()
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"ok": True, "registered": False}
    assert not device.is_active


@pytest.mark.django_db
def test_sends_private_notification_to_all_recipient_devices(
    participant_pair,
    push_delivery_settings,
):
    PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=SECOND_FID,
    )
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=SENDER_FID,
    )
    send_result = SimpleNamespace(
        success_count=2,
        responses=[
            SimpleNamespace(success=True, exception=None),
            SimpleNamespace(success=True, exception=None),
        ],
    )

    with (
        patch(
            "apps.ratings.notifications._get_firebase_app",
            return_value=object(),
        ),
        patch(
            "apps.ratings.notifications.messaging.send_each_for_multicast",
            return_value=send_result,
        ) as send_each_for_multicast,
    ):
        sent_count = send_score_change_notification(
            recipient_id=participant_pair.second.pk
        )

    assert sent_count == 2
    message = send_each_for_multicast.call_args.args[0]
    assert sorted(message.fids) == sorted([VALID_FID, SECOND_FID])
    assert message.notification.title == "우리 사이"
    assert message.notification.body == "새로운 마음 기록이 도착했어요"
    assert message.webpush.fcm_options.link == TEST_PUBLIC_BASE_URL


@pytest.mark.django_db
def test_permanently_invalid_fid_is_deactivated(
    participant_pair,
    push_delivery_settings,
):
    device = PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    send_result = SimpleNamespace(
        success_count=0,
        responses=[
            SimpleNamespace(
                success=False,
                exception=messaging.UnregisteredError("unregistered"),
            ),
        ],
    )

    with (
        patch(
            "apps.ratings.notifications._get_firebase_app",
            return_value=object(),
        ),
        patch(
            "apps.ratings.notifications.messaging.send_each_for_multicast",
            return_value=send_result,
        ),
    ):
        send_score_change_notification(recipient_id=participant_pair.second.pk)

    device.refresh_from_db()
    assert not device.is_active


@pytest.mark.django_db
def test_score_change_notifies_recipient_only_after_commit(
    participant_pair,
    push_delivery_settings,
    django_capture_on_commit_callbacks,
):
    with patch(
        "apps.ratings.services.score_changes.send_score_change_notification"
    ) as send_push:
        with django_capture_on_commit_callbacks(execute=True):
            change_relationship_score(
                source_participant=participant_pair.first,
                delta=1,
            )
            send_push.assert_not_called()

        send_push.assert_called_once_with(recipient_id=participant_pair.second.pk)


@pytest.mark.django_db
def test_push_failure_does_not_undo_score_change(
    participant_pair,
    push_delivery_settings,
    django_capture_on_commit_callbacks,
):
    PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    with (
        patch(
            "apps.ratings.notifications._get_firebase_app",
            return_value=object(),
        ),
        patch(
            "apps.ratings.notifications.messaging.send_each_for_multicast",
            side_effect=RuntimeError("FCM unavailable"),
        ) as send_each_for_multicast,
        django_capture_on_commit_callbacks(execute=True),
    ):
        change_relationship_score(
            source_participant=participant_pair.first,
            delta=3,
        )

    participant_pair.first_to_second.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 3
    send_each_for_multicast.assert_called_once()


def test_service_worker_uses_environment_firebase_config(client, settings):
    settings.PUSH_NOTIFICATIONS_AVAILABLE = True
    settings.FIREBASE_WEB_CONFIG = {
        "apiKey": "test-api-key",
        "appId": "test-app-id",
        "authDomain": "test.firebaseapp.com",
        "messagingSenderId": "123456",
        "projectId": "test-project",
    }

    response = client.get(reverse("service-worker"))
    body = response.content.decode()

    assert '"projectId":"test-project"' in body
    assert "woorisai-friendly-rating" not in body


@pytest.mark.django_db
def test_mismatched_service_account_project_is_rejected(
    participant_pair,
    settings,
):
    PushDevice.objects.create(
        participant=participant_pair.second,
        firebase_installation_id=VALID_FID,
    )
    settings.PUSH_NOTIFICATIONS_ENABLED = True
    settings.FIREBASE_WEB_CONFIG = {"projectId": "web-project"}
    settings.FIREBASE_SERVICE_ACCOUNT_JSON = json.dumps(
        {"project_id": "different-project"}
    )

    with patch(
        "apps.ratings.notifications.messaging.send_each_for_multicast"
    ) as send_each_for_multicast:
        sent_count = send_score_change_notification(
            recipient_id=participant_pair.second.pk
        )

    assert sent_count == 0
    send_each_for_multicast.assert_not_called()
