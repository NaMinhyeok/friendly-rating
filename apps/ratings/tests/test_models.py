from django.core.exceptions import ValidationError
from django.test import TestCase

from ..models import ScoreChange
from .factories import create_participant_pair


class RelationshipScoreModelTests(TestCase):
    def test_scores_are_directional(self):
        first, second, first_to_second, second_to_first = create_participant_pair()

        self.assertEqual(first_to_second.source_participant, first)
        self.assertEqual(first_to_second.target_participant, second)
        self.assertEqual(second_to_first.source_participant, second)
        self.assertEqual(second_to_first.target_participant, first)
        self.assertEqual(first_to_second.current_score, 0)
        self.assertEqual(second_to_first.current_score, 0)


class ScoreChangeModelTests(TestCase):
    def setUp(self):
        self.first, _, self.score, _ = create_participant_pair()
        self.change = ScoreChange.objects.create(
            relationship_score=self.score,
            changed_by=self.first,
            delta=5,
            reason="고마운 일이 있었어요",
            resulting_score=5,
        )

    def test_existing_change_cannot_be_saved(self):
        self.change.reason = "바꾼 이유"

        with self.assertRaisesMessage(ValidationError, "수정할 수 없습니다"):
            self.change.save()

    def test_existing_change_cannot_be_deleted(self):
        with self.assertRaisesMessage(ValidationError, "삭제할 수 없습니다"):
            self.change.delete()

    def test_changes_cannot_be_bulk_updated(self):
        with self.assertRaisesMessage(ValidationError, "수정할 수 없습니다"):
            ScoreChange.objects.filter(pk=self.change.pk).update(reason="바꾼 이유")
