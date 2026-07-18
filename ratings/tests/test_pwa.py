import json
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse

from .factories import create_participant_pair


class PwaAssetTests(TestCase):
    def test_manifest_declares_standalone_app_and_required_icons(self):
        manifest_path = finders.find("ratings/manifest.webmanifest")
        self.assertIsNotNone(manifest_path)

        manifest = json.loads(Path(manifest_path).read_text())

        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertIn("192x192", {icon["sizes"] for icon in manifest["icons"]})
        self.assertIn("512x512", {icon["sizes"] for icon in manifest["icons"]})
        self.assertIn("maskable", {icon["purpose"] for icon in manifest["icons"]})

    def test_service_worker_only_runtime_caches_static_requests(self):
        response = self.client.get(reverse("service-worker"))
        body = response.content.decode()

        self.assertIn('request.mode === "navigate"', body)
        self.assertIn('url.pathname.startsWith("/static/")', body)
        self.assertNotIn('caches.open("/")', body)
        self.assertNotIn('caches.open("/history/")', body)


class PwaPageTests(TestCase):
    def setUp(self):
        self.first, _, _, _ = create_participant_pair()
        self.client.force_login(self.first.user)

    def test_home_includes_installable_pwa_metadata(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, 'apple-mobile-web-app-capable')

    def test_notification_client_is_hidden_when_push_is_unavailable(self):
        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "data-notification-settings")
        self.assertNotContains(response, "ratings/notifications.js")

    @override_settings(
        PUSH_NOTIFICATIONS_AVAILABLE=True,
        FIREBASE_WEB_CONFIG={
            "apiKey": "test-api-key",
            "appId": "test-app-id",
            "authDomain": "test.firebaseapp.com",
            "messagingSenderId": "123456",
            "projectId": "test-project",
        },
        FIREBASE_VAPID_PUBLIC_KEY="B" + "A" * 86,
    )
    def test_notification_client_receives_only_public_configuration(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, "data-notification-settings")
        self.assertContains(response, "ratings/notifications.js")
        self.assertContains(response, "test-project")
        self.assertNotContains(response, "private_key")
