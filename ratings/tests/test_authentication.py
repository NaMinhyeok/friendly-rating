from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from ratings.models import Participant, RelationshipScore


PARTICIPANT_ENV = {
    "PARTICIPANT_1_NAME": "민수",
    "PARTICIPANT_1_PIN": "1234",
    "PARTICIPANT_2_NAME": "지수",
    "PARTICIPANT_2_PIN": "5678",
}


class ProvisionParticipantsCommandTests(TestCase):
    def run_command(self):
        with patch.dict("os.environ", PARTICIPANT_ENV, clear=False):
            call_command("provision_participants", stdout=StringIO())

    def test_command_creates_two_participants_and_directional_scores(self):
        self.run_command()

        participants = list(Participant.objects.select_related("user"))
        self.assertEqual([participant.display_name for participant in participants], ["민수", "지수"])
        self.assertEqual(RelationshipScore.objects.count(), 2)
        self.assertTrue(participants[0].user.check_password("1234"))
        self.assertTrue(participants[1].user.check_password("5678"))

    def test_command_is_idempotent(self):
        self.run_command()
        self.run_command()

        self.assertEqual(Participant.objects.count(), 2)
        self.assertEqual(get_user_model().objects.filter(username__startswith="participant-").count(), 2)
        self.assertEqual(RelationshipScore.objects.count(), 2)

    def test_command_rejects_invalid_pin(self):
        invalid_environment = {**PARTICIPANT_ENV, "PARTICIPANT_1_PIN": "12ab"}

        with patch.dict("os.environ", invalid_environment, clear=False):
            with self.assertRaisesMessage(CommandError, "숫자 4자리"):
                call_command("provision_participants", stdout=StringIO())


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
        self.assertEqual(int(self.client.session["_auth_user_id"]), participant.user_id)

    def test_invalid_pin_is_rejected(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)

        response = self.client.post(
            reverse("login"),
            {"participant": participant.pk, "pin": "9999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PIN 번호가 올바르지 않습니다")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, f'{reverse("login")}?next={reverse("home")}')

    def test_logout_requires_post_and_clears_session(self):
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        self.client.force_login(participant.user)

        get_response = self.client.get(reverse("logout"))
        post_response = self.client.post(reverse("logout"))

        self.assertEqual(get_response.status_code, 405)
        self.assertRedirects(post_response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)
