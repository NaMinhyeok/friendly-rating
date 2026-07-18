from django.core.exceptions import ValidationError
from django.test import TestCase

from ratings.models import ScoreChange
from ratings.services import change_relationship_score

from .factories import create_participant_pair


class ChangeRelationshipScoreTests(TestCase):
    def setUp(self):
        self.first, _, self.score, _ = create_participant_pair()

    def test_increase_updates_score_and_creates_history(self):
        change = change_relationship_score(
            rater=self.first,
            delta=7,
            reason="  오늘 많이 도와줘서  ",
        )

        self.score.refresh_from_db()
        self.assertEqual(self.score.value, 7)
        self.assertEqual(change.delta, 7)
        self.assertEqual(change.reason, "오늘 많이 도와줘서")
        self.assertEqual(change.resulting_score, 7)
        self.assertEqual(change.changed_by, self.first)

    def test_decrease_updates_only_raters_direction(self):
        self.score.value = 10
        self.score.save()

        change_relationship_score(
            rater=self.first,
            delta=-3,
            reason="조금 서운했어요",
        )

        self.score.refresh_from_db()
        self.assertEqual(self.score.value, 7)

    def test_out_of_range_change_rolls_back_without_history(self):
        with self.assertRaisesMessage(ValidationError, "0점보다 낮거나"):
            change_relationship_score(
                rater=self.first,
                delta=-1,
                reason="범위를 벗어나요",
            )

        self.score.refresh_from_db()
        self.assertEqual(self.score.value, 0)
        self.assertFalse(ScoreChange.objects.exists())

    def test_reason_is_required(self):
        with self.assertRaisesMessage(ValidationError, "이유를 입력"):
            change_relationship_score(rater=self.first, delta=1, reason="   ")

    def test_delta_must_be_non_zero_integer(self):
        with self.assertRaisesMessage(ValidationError, "0이 아닌 정수"):
            change_relationship_score(rater=self.first, delta=0, reason="이유")
