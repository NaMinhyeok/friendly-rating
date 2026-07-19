import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse
from firebase_admin import messaging

from ..models import PushDevice
from ..notifications import (
    send_score_change_notification,
    send_score_comment_notification,
)
from ..services import change_relationship_score

VALID_FID = "c12345678901234567890A"
SECOND_FID = "d12345678901234567890B"
SENDER_FID = "e12345678901234567890C"
TEST_PUBLIC_BASE_URL = "https://friendly-rating.example.test/"


@pytest.fixture
def push_delivery_settings(settings):
    settings.PUSH_NOTIFICATIONS_ENABLED = True
    settings.PUBLIC_BASE_URL = TEST_PUBLIC_BASE_URL


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
            recipient_id=participant_pair.second.pk,
            score_change_id=42,
        )

    assert sent_count == 2
    message = send_each_for_multicast.call_args.args[0]
    assert sorted(message.fids) == sorted([VALID_FID, SECOND_FID])
    assert message.notification.title == "우리 사이"
    assert message.notification.body == "새로운 마음 기록이 도착했어요"
    assert message.webpush.fcm_options.link == (f"{TEST_PUBLIC_BASE_URL}history/42/")


@pytest.mark.django_db
def test_sends_private_comment_notification_to_the_thread(
    participant_pair,
    push_delivery_settings,
):
    PushDevice.objects.create(
        participant=participant_pair.first,
        firebase_installation_id=VALID_FID,
    )
    send_result = SimpleNamespace(
        success_count=1,
        responses=[SimpleNamespace(success=True, exception=None)],
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
        sent_count = send_score_comment_notification(
            recipient_id=participant_pair.first.pk,
            score_change_id=43,
        )

    assert sent_count == 1
    message = send_each_for_multicast.call_args.args[0]
    assert message.fids == [VALID_FID]
    assert message.notification.title == "우리 사이"
    assert message.notification.body == "새로운 댓글이 도착했어요"
    assert message.webpush.fcm_options.link == (f"{TEST_PUBLIC_BASE_URL}history/43/")


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
        send_score_change_notification(
            recipient_id=participant_pair.second.pk,
            score_change_id=42,
        )

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
            change = change_relationship_score(
                source_participant=participant_pair.first,
                delta=1,
            )
            send_push.assert_not_called()

        send_push.assert_called_once_with(
            recipient_id=participant_pair.second.pk,
            score_change_id=change.pk,
        )


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
            recipient_id=participant_pair.second.pk,
            score_change_id=42,
        )

    assert sent_count == 0
    send_each_for_multicast.assert_not_called()
