from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse


class BasicViewTests(TestCase):
    def test_health_check_is_available(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertEqual(response.headers["Content-Type"], "text/plain")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_check_bypasses_https_redirect_for_railway(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)

    @patch("ratings.views.database_is_ready", return_value=False)
    def test_health_check_reports_database_unavailability(self, _probe):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    @patch("ratings.views.database_is_ready")
    def test_health_check_rejects_non_get_requests(self, probe):
        response = self.client.post(reverse("health"))

        self.assertEqual(response.status_code, 405)
        probe.assert_not_called()
