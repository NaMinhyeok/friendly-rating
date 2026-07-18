from django.test import SimpleTestCase
from django.urls import reverse


class BasicViewTests(SimpleTestCase):
    def test_home_is_available(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Friendly Rating")

    def test_health_check_is_available(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
