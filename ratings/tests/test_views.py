from django.test import SimpleTestCase, override_settings
from django.urls import reverse


class BasicViewTests(SimpleTestCase):
    def test_health_check_is_available(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_check_bypasses_https_redirect_for_railway(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
