import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from firebase_admin import messaging

from ratings.models import PushDevice
from ratings.notifications import _get_firebase_app, send_score_change_notification
from ratings.services import change_relationship_score

from .factories import create_participant_pair


VALID_FID = "c12345678901234567890A"
SECOND_FID = "d12345678901234567890B"


class PushDeviceViewTests(TestCase):
    def setUp(self):
        self.first, self.second, _, _ = create_participant_pair()

    def post_json(self, url_name, fid=VALID_FID):
        return self.client.post(
            reverse(url_name),
            data=json.dumps({"fid": fid}),
            content_type="application/json",
        )

    def test_registration_requires_login(self):
        response = self.post_json("register-push-device")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PushDevice.objects.exists())

    def test_registration_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.first.user)

        response = csrf_client.post(
            reverse("register-push-device"),
            data=json.dumps({"fid": VALID_FID}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushDevice.objects.exists())

    def test_participant_can_register_multiple_devices(self):
        self.client.force_login(self.first.user)

        first_response = self.post_json("register-push-device")
        second_response = self.post_json("register-push-device", SECOND_FID)

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(self.first.push_devices.filter(active=True).count(), 2)

    def test_registration_reactivates_and_reassigns_a_fid(self):
        PushDevice.objects.create(
            participant=self.second,
            fid=VALID_FID,
            active=False,
        )
        self.client.force_login(self.first.user)

        response = self.post_json("register-push-device")

        device = PushDevice.objects.get(fid=VALID_FID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(device.participant, self.first)
        self.assertTrue(device.active)

    def test_registration_keeps_only_the_five_most_recent_devices(self):
        self.client.force_login(self.first.user)

        for index in range(6):
            response = self.post_json(
                "register-push-device",
                f"c{'A' * 20}{index}",
            )
            self.assertEqual(response.status_code, 201)

        devices = self.first.push_devices.order_by("updated_at")
        self.assertEqual(devices.count(), 5)
        self.assertFalse(devices.filter(fid=f"c{'A' * 20}0").exists())

    def test_participant_cannot_unregister_another_participants_device(self):
        device = PushDevice.objects.create(
            participant=self.second,
            fid=VALID_FID,
        )
        self.client.force_login(self.first.user)

        response = self.post_json("unregister-push-device")

        device.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(device.active)

    def test_invalid_fid_is_rejected(self):
        self.client.force_login(self.first.user)

        response = self.post_json("register-push-device", "not valid")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PushDevice.objects.exists())

    def test_structurally_invalid_url_safe_fid_is_rejected(self):
        self.client.force_login(self.first.user)

        response = self.post_json(
            "register-push-device",
            "a12345678901234567890A",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PushDevice.objects.exists())

    def test_owned_device_can_be_unregistered(self):
        device = PushDevice.objects.create(
            participant=self.first,
            fid=VALID_FID,
        )
        self.client.force_login(self.first.user)

        response = self.post_json("unregister-push-device")

        device.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(device.active)


@override_settings(
    PUSH_NOTIFICATIONS_ENABLED=True,
    PUBLIC_BASE_URL="https://woorisai.up.railway.app/",
)
class PushDeliveryTests(TestCase):
    def setUp(self):
        self.first, self.second, self.score, _ = create_participant_pair()

    @patch("ratings.notifications.messaging.send_each_for_multicast")
    @patch("ratings.notifications._get_firebase_app")
    def test_sends_private_notification_to_all_recipient_devices(
        self,
        get_firebase_app,
        send_each_for_multicast,
    ):
        get_firebase_app.return_value = object()
        PushDevice.objects.create(participant=self.second, fid=VALID_FID)
        PushDevice.objects.create(participant=self.second, fid=SECOND_FID)
        send_each_for_multicast.return_value = SimpleNamespace(
            success_count=2,
            responses=[
                SimpleNamespace(success=True, exception=None),
                SimpleNamespace(success=True, exception=None),
            ],
        )

        sent_count = send_score_change_notification(recipient_id=self.second.pk)

        self.assertEqual(sent_count, 2)
        message = send_each_for_multicast.call_args.args[0]
        self.assertCountEqual(message.fids, [VALID_FID, SECOND_FID])
        self.assertEqual(message.notification.title, "우리 사이")
        self.assertEqual(
            message.notification.body,
            "새로운 마음 기록이 도착했어요",
        )
        self.assertEqual(
            message.webpush.fcm_options.link,
            "https://woorisai.up.railway.app/",
        )

    @patch("ratings.notifications.messaging.send_each_for_multicast")
    @patch("ratings.notifications._get_firebase_app")
    def test_permanently_invalid_fid_is_deactivated(
        self,
        get_firebase_app,
        send_each_for_multicast,
    ):
        get_firebase_app.return_value = object()
        device = PushDevice.objects.create(participant=self.second, fid=VALID_FID)
        send_each_for_multicast.return_value = SimpleNamespace(
            success_count=0,
            responses=[
                SimpleNamespace(
                    success=False,
                    exception=messaging.UnregisteredError("unregistered"),
                ),
            ],
        )

        send_score_change_notification(recipient_id=self.second.pk)

        device.refresh_from_db()
        self.assertFalse(device.active)

    @patch("ratings.services.send_score_change_notification")
    def test_score_change_notifies_recipient_only_after_commit(self, send_push):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            change_relationship_score(rater=self.first, delta=1)

        send_push.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        send_push.assert_called_once_with(recipient_id=self.second.pk)

    @patch(
        "ratings.services.send_score_change_notification",
        side_effect=RuntimeError("FCM unavailable"),
    )
    def test_push_failure_does_not_undo_score_change(self, _send_push):
        with self.assertLogs("ratings.services", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                change_relationship_score(rater=self.first, delta=3)

        self.score.refresh_from_db()
        self.assertEqual(self.score.value, 3)


class ServiceWorkerViewTests(TestCase):
    def test_service_worker_is_root_scoped_and_not_cached(self):
        response = self.client.get(reverse("service-worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(
            response["Cache-Control"],
            "no-cache, no-store, must-revalidate",
        )

    @override_settings(
        PUSH_NOTIFICATIONS_AVAILABLE=True,
        FIREBASE_WEB_CONFIG={
            "apiKey": "test-api-key",
            "appId": "test-app-id",
            "authDomain": "test.firebaseapp.com",
            "messagingSenderId": "123456",
            "projectId": "test-project",
        },
    )
    def test_service_worker_uses_environment_firebase_config(self):
        response = self.client.get(reverse("service-worker"))
        body = response.content.decode()

        self.assertIn('"projectId":"test-project"', body)
        self.assertNotIn("woorisai-friendly-rating", body)


class FirebaseConfigurationTests(TestCase):
    @override_settings(
        PUSH_NOTIFICATIONS_ENABLED=True,
        FIREBASE_WEB_CONFIG={"projectId": "web-project"},
        FIREBASE_SERVICE_ACCOUNT_JSON=json.dumps(
            {"project_id": "different-project"}
        ),
    )
    def test_mismatched_service_account_project_is_rejected(self):
        with self.assertLogs("ratings.notifications", level="ERROR"):
            firebase_app = _get_firebase_app()

        self.assertIsNone(firebase_app)
