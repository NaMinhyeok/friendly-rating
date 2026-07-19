from io import StringIO
from unittest.mock import patch

from axes.models import AccessAttempt
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import Participant
from ..security import get_client_ip_address
from .http_helpers import csrf_token_from_form

PARTICIPANT_ENV = {
    "PARTICIPANT_1_NAME": "민수",
    "PARTICIPANT_1_PIN": "1234",
    "PARTICIPANT_2_NAME": "지수",
    "PARTICIPANT_2_PIN": "5678",
}


class PinLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict("os.environ", PARTICIPANT_ENV, clear=False):
            call_command("provision_participants", stdout=StringIO())

    def test_login_page_lists_both_participants(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "민수")
        self.assertContains(response, "지수")

    def test_valid_pin_logs_participant_in(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)

        response = self.client.post(
            reverse("login"),
            {"participant": participant.pk, "pin": "1234"},
        )

        self.assertRedirects(response, reverse("home"))
        home_response = self.client.get(reverse("home"))
        self.assertContains(home_response, "민수님의 마음 공간")

    def test_login_requires_csrf_and_does_not_create_a_session(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("login"),
            {"participant": participant.pk, "pin": "1234"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertRedirects(
            csrf_client.get(reverse("home")),
            f"{reverse('login')}?next={reverse('home')}",
        )

    def test_login_accepts_the_rendered_csrf_token(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        csrf_client = Client(enforce_csrf_checks=True)
        login_response = csrf_client.get(reverse("login"))
        csrf_token = csrf_token_from_form(login_response, None)

        response = csrf_client.post(
            reverse("login"),
            {
                "participant": participant.pk,
                "pin": "1234",
                "csrfmiddlewaretoken": csrf_token,
            },
            HTTP_ORIGIN="http://testserver",
        )

        self.assertRedirects(response, reverse("home"))
        self.assertContains(
            csrf_client.get(reverse("home")),
            "민수님의 마음 공간",
        )

    def test_invalid_pin_is_rejected(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)

        response = self.client.post(
            reverse("login"),
            {"participant": participant.pk, "pin": "9999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PIN 번호가 올바르지 않습니다")
        self.assertRedirects(
            self.client.get(reverse("home")),
            f"{reverse('login')}?next={reverse('home')}",
        )

    def test_account_is_locked_after_failures_from_different_ips(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)

        response = None
        for attempt in range(1, 6):
            response = self.client.post(
                reverse("login"),
                {"participant": participant.pk, "pin": "9999"},
                REMOTE_ADDR=f"192.0.2.{attempt}",
            )

        assert response is not None
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "900")
        self.assertContains(
            response,
            "로그인 시도가 너무 많습니다",
            status_code=429,
        )

        locked_response = Client().post(
            reverse("login"),
            {"participant": participant.pk, "pin": "1234"},
            REMOTE_ADDR="192.0.2.100",
        )
        other_participant = Participant.objects.get(slot=Participant.Slot.SECOND)
        unaffected_response = Client().post(
            reverse("login"),
            {"participant": other_participant.pk, "pin": "5678"},
            REMOTE_ADDR="198.51.100.1",
        )

        self.assertEqual(locked_response.status_code, 429)
        self.assertRedirects(unaffected_response, reverse("home"))

    def test_ip_is_locked_after_failures_across_accounts(self):
        participants = list(Participant.objects.order_by("slot"))

        for attempt in range(5):
            response = self.client.post(
                reverse("login"),
                {
                    "participant": participants[attempt % 2].pk,
                    "pin": "9999",
                },
                REMOTE_ADDR="192.0.2.10",
            )

        blocked_response = Client().post(
            reverse("login"),
            {"participant": participants[1].pk, "pin": "5678"},
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(blocked_response.status_code, 429)

    def test_successful_login_resets_failure_count(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        login_url = reverse("login")

        for _ in range(4):
            response = self.client.post(
                login_url,
                {"participant": participant.pk, "pin": "9999"},
                REMOTE_ADDR="192.0.2.20",
            )
            self.assertEqual(response.status_code, 200)

        success_response = self.client.post(
            login_url,
            {"participant": participant.pk, "pin": "1234"},
            REMOTE_ADDR="192.0.2.20",
        )
        self.assertRedirects(success_response, reverse("home"))

        fresh_client = Client()
        for _ in range(4):
            response = fresh_client.post(
                login_url,
                {"participant": participant.pk, "pin": "9999"},
                REMOTE_ADDR="192.0.2.20",
            )

        self.assertEqual(response.status_code, 200)

    def test_login_is_allowed_after_cooloff(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        login_url = reverse("login")

        for _ in range(5):
            response = self.client.post(
                login_url,
                {"participant": participant.pk, "pin": "9999"},
                REMOTE_ADDR="192.0.2.30",
            )

        self.assertEqual(response.status_code, 429)
        AccessAttempt.objects.update(
            attempt_time=timezone.now() - timezone.timedelta(minutes=16)
        )

        response = Client().post(
            login_url,
            {"participant": participant.pk, "pin": "1234"},
            REMOTE_ADDR="192.0.2.30",
        )

        self.assertRedirects(response, reverse("home"))

    def test_pin_is_not_stored_in_failure_records(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)

        self.client.post(
            reverse("login"),
            {"participant": participant.pk, "pin": "9999"},
            REMOTE_ADDR="192.0.2.40",
        )

        post_data = AccessAttempt.objects.get().post_data
        self.assertNotIn("9999", post_data)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('home')}")

    def test_logout_requires_post_and_clears_session(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        self.client.force_login(participant.user)

        get_response = self.client.get(reverse("logout"))
        home_after_get = self.client.get(reverse("home"))
        post_response = self.client.post(reverse("logout"))

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(home_after_get.status_code, 200)
        self.assertRedirects(post_response, reverse("login"))
        self.assertRedirects(
            self.client.get(reverse("home")),
            f"{reverse('login')}?next={reverse('home')}",
        )

    def test_logout_requires_csrf_and_preserves_the_session_on_failure(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(participant.user)

        response = csrf_client.post(reverse("logout"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(csrf_client.get(reverse("home")).status_code, 200)

    def test_logout_accepts_a_valid_csrf_token(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(participant.user)
        home_response = csrf_client.get(reverse("home"))
        csrf_token = csrf_token_from_form(home_response, reverse("logout"))

        response = csrf_client.post(
            reverse("logout"),
            {"csrfmiddlewaretoken": csrf_token},
            HTTP_ORIGIN="http://testserver",
        )

        self.assertEqual(home_response.status_code, 200)
        self.assertRedirects(response, reverse("login"))
        self.assertRedirects(
            csrf_client.get(reverse("home")),
            f"{reverse('login')}?next={reverse('home')}",
        )


def test_local_request_uses_remote_address_only(rf, settings):
    settings.IS_RAILWAY = False
    request = rf.get(
        "/",
        REMOTE_ADDR="192.0.2.50",
        HTTP_X_REAL_IP="198.51.100.50",
        HTTP_X_FORWARDED_FOR="203.0.113.50",
    )

    assert get_client_ip_address(request) == "192.0.2.50"


def test_railway_request_uses_x_real_ip(rf, settings):
    settings.IS_RAILWAY = True
    request = rf.get(
        "/",
        REMOTE_ADDR="10.0.0.1",
        HTTP_X_REAL_IP="2001:db8::1",
        HTTP_X_FORWARDED_FOR="203.0.113.50",
    )

    assert get_client_ip_address(request) == "2001:db8::1"


def test_invalid_railway_ip_is_rejected(rf, settings):
    settings.IS_RAILWAY = True
    request = rf.get("/", HTTP_X_REAL_IP="not-an-ip")

    assert get_client_ip_address(request) is None
