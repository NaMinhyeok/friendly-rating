import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
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


@pytest.mark.django_db
def test_registration_requires_login(client):
    response = _post_json(client, "register-push-device")

    assert response.status_code == 302
    assert not PushDevice.objects.exists()


@pytest.mark.django_db
def test_registration_requires_csrf(participant_pair):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(participant_pair.first.user)

    response = _post_json(csrf_client, "register-push-device")

    assert response.status_code == 403
    assert not PushDevice.objects.exists()


@pytest.mark.django_db
def test_participant_can_register_multiple_devices(client, participant_pair):
    client.force_login(participant_pair.first.user)

    first_response = _post_json(client, "register-push-device")
    second_response = _post_json(client, "register-push-device", SECOND_FID)

    assert first_response.status_code == 201
    assert second_response.status_code == 201
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
    assert device.is_active


@pytest.mark.parametrize(
    "fid",
    [
        pytest.param("not valid", id="not-url-safe"),
        pytest.param("a12345678901234567890A", id="invalid-prefix"),
    ],
)
@pytest.mark.django_db
def test_invalid_fid_is_rejected(client, participant_pair, fid):
    client.force_login(participant_pair.first.user)

    response = _post_json(client, "register-push-device", fid)

    assert response.status_code == 400
    assert not PushDevice.objects.exists()


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
