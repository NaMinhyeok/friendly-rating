from django.test import TestCase
from django.urls import reverse

from ..models import ScoreChange
from .factories import create_participant_pair


class ScoreDashboardTests(TestCase):
    def setUp(self):
        (
            self.first,
            self.second,
            self.first_to_second,
            self.second_to_first,
        ) = create_participant_pair()
        self.client.force_login(self.first.user)

    def test_dashboard_shows_both_directional_scores(self):
        self.first_to_second.current_score = 12
        self.first_to_second.save()
        self.second_to_first.current_score = 34
        self.second_to_first.save()

        response = self.client.get(reverse("home"))

        self.assertContains(response, "첫 번째 → 두 번째")
        self.assertContains(response, "두 번째 → 첫 번째")
        self.assertContains(response, "12점")
        self.assertContains(response, "34점")

    def test_participant_can_increase_only_their_outgoing_score(self):
        response = self.client.post(
            reverse("change-score"),
            {
                "operation": "increase",
                "amount": 5,
                "reason": "오늘 많이 도와줘서",
            },
        )

        self.assertRedirects(response, reverse("home"))
        self.first_to_second.refresh_from_db()
        self.second_to_first.refresh_from_db()
        self.assertEqual(self.first_to_second.current_score, 5)
        self.assertEqual(self.second_to_first.current_score, 0)
        change = ScoreChange.objects.get()
        self.assertEqual(change.changed_by, self.first)
        self.assertEqual(change.delta, 5)
        self.assertEqual(change.reason, "오늘 많이 도와줘서")
        self.assertEqual(change.resulting_score, 5)

    def test_participant_can_decrease_their_score(self):
        self.first_to_second.current_score = 10
        self.first_to_second.save()

        response = self.client.post(
            reverse("change-score"),
            {
                "operation": "decrease",
                "amount": 3,
                "reason": "조금 서운했어요",
            },
        )

        self.assertRedirects(response, reverse("home"))
        self.first_to_second.refresh_from_db()
        self.assertEqual(self.first_to_second.current_score, 7)
        self.assertEqual(ScoreChange.objects.get().delta, -3)

    def test_out_of_range_change_shows_error_without_writing(self):
        response = self.client.post(
            reverse("change-score"),
            {
                "operation": "decrease",
                "amount": 1,
                "reason": "범위 밖 변경",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "0점보다 낮거나", status_code=400)
        self.first_to_second.refresh_from_db()
        self.assertEqual(self.first_to_second.current_score, 0)
        self.assertFalse(ScoreChange.objects.exists())

    def test_reason_can_be_left_blank(self):
        response = self.client.post(
            reverse("change-score"),
            {"operation": "increase", "amount": 1},
        )

        self.assertRedirects(response, reverse("home"))
        self.first_to_second.refresh_from_db()
        self.assertEqual(self.first_to_second.current_score, 1)
        self.assertEqual(ScoreChange.objects.get().reason, "")

    def test_reason_is_limited_to_200_characters(self):
        response = self.client.post(
            reverse("change-score"),
            {"operation": "increase", "amount": 1, "reason": "가" * 201},
        )

        self.assertContains(response, "200자", status_code=400)
        self.assertFalse(ScoreChange.objects.exists())

    def test_score_change_requires_post(self):
        response = self.client.get(reverse("change-score"))

        self.assertEqual(response.status_code, 405)


class ScoreHistoryTests(TestCase):
    def setUp(self):
        self.first, self.second, self.first_to_second, _ = create_participant_pair()
        self.client.force_login(self.second.user)

    def create_change(self, number, *, reason=None):
        return ScoreChange.objects.create(
            relationship_score=self.first_to_second,
            changed_by=self.first,
            delta=1,
            reason=f"변경 이유 {number}" if reason is None else reason,
            resulting_score=1,
        )

    def test_both_participants_can_see_complete_change_details(self):
        self.create_change(1)

        response = self.client.get(reverse("history"))

        self.assertContains(response, "첫 번째 → 두 번째")
        self.assertContains(response, "+1점")
        self.assertContains(response, "변경자 첫 번째")
        self.assertContains(response, "변경 이유 1")
        self.assertContains(response, "변경 후 1점")

    def test_blank_reason_does_not_render_empty_quotes(self):
        self.create_change(1, reason="")

        response = self.client.get(reverse("history"))

        self.assertNotContains(response, '<p class="history-reason">')

    def test_history_is_paginated_twenty_per_page(self):
        for number in range(21):
            self.create_change(number)

        first_page = self.client.get(reverse("history"))
        second_page = self.client.get(reverse("history"), {"page": 2})

        self.assertEqual(len(first_page.context["page"].object_list), 20)
        self.assertEqual(len(second_page.context["page"].object_list), 1)

    def test_history_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("history"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('history')}",
        )
