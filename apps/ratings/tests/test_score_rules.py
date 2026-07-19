from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from ..score_rules import calculate_resulting_score, prepare_score_change


class ScoreChangeRulesTests(SimpleTestCase):
    def calculate(self, *, current_score, delta, reason=""):
        change = prepare_score_change(delta=delta, reason=reason)
        return change, calculate_resulting_score(
            current_score=current_score,
            change=change,
        )

    def test_calculates_scores_at_both_boundaries(self):
        cases = (
            {"current_score": 1, "delta": -1, "expected": 0},
            {"current_score": 99, "delta": 1, "expected": 100},
        )

        for case in cases:
            with self.subTest(case=case):
                change, resulting_score = self.calculate(
                    current_score=case["current_score"],
                    delta=case["delta"],
                )

                self.assertEqual(resulting_score, case["expected"])
                self.assertEqual(change.delta, case["delta"])

    def test_rejects_scores_outside_the_allowed_range(self):
        cases = (
            {"current_score": 0, "delta": -1},
            {"current_score": 100, "delta": 1},
        )

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaisesMessage(
                    ValidationError,
                    "친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다.",
                ):
                    self.calculate(**case)

    def test_rejects_zero_boolean_and_non_integer_deltas(self):
        for delta in (0, True, False, 1.0, "1", None):
            with self.subTest(delta=delta):
                with self.assertRaisesMessage(
                    ValidationError,
                    "변경 점수는 0이 아닌 정수여야 합니다.",
                ):
                    prepare_score_change(delta=delta)

    def test_normalizes_surrounding_reason_whitespace(self):
        change = prepare_score_change(
            delta=1,
            reason="  오늘  정말 고마웠어요\n",
        )

        self.assertEqual(change.reason, "오늘  정말 고마웠어요")

    def test_normalizes_a_whitespace_only_reason_to_empty(self):
        change = prepare_score_change(
            delta=1,
            reason=" \t\n ",
        )

        self.assertEqual(change.reason, "")

    def test_accepts_two_hundred_reason_characters_after_normalizing(self):
        change = prepare_score_change(
            delta=1,
            reason=f" {'가' * 200} ",
        )

        self.assertEqual(change.reason, "가" * 200)

    def test_rejects_more_than_two_hundred_reason_characters(self):
        with self.assertRaisesMessage(
            ValidationError,
            "변경 이유는 200자 이하여야 합니다.",
        ):
            prepare_score_change(
                delta=1,
                reason="가" * 201,
            )

    def test_rejects_a_non_string_reason(self):
        with self.assertRaisesMessage(
            ValidationError,
            "변경 이유는 문자열이어야 합니다.",
        ):
            prepare_score_change(delta=1, reason=None)

    def test_validates_delta_before_reason(self):
        with self.assertRaisesMessage(
            ValidationError,
            "변경 점수는 0이 아닌 정수여야 합니다.",
        ):
            prepare_score_change(delta=0, reason=None)
