from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from ..models import Participant, ScoreChange
from ..services import change_relationship_score
from .factories import create_participant_pair


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentScoreChangeTests(TransactionTestCase):
    def setUp(self):
        self.first, _, self.score, _ = create_participant_pair()

    def test_concurrent_changes_are_serialized_by_row_lock(self):
        start = Barrier(3)

        def change_score():
            close_old_connections()
            try:
                source_participant = Participant.objects.get(pk=self.first.pk)
                with CaptureQueriesContext(connection) as captured_queries:
                    start.wait(timeout=5)
                    change = change_relationship_score(
                        source_participant=source_participant,
                        delta=1,
                    )
                sql = [query["sql"] for query in captured_queries]
                return change.resulting_score, sql
            finally:
                connections["default"].close()

        with (
            patch("apps.ratings.services.score_changes.send_score_change_notification"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [executor.submit(change_score) for _ in range(2)]
            start.wait(timeout=5)
            results = [future.result(timeout=10) for future in futures]

        self.score.refresh_from_db()
        resulting_scores = sorted(result[0] for result in results)
        recorded_scores = sorted(
            ScoreChange.objects.values_list("resulting_score", flat=True)
        )

        self.assertEqual(self.score.current_score, 2)
        self.assertEqual(resulting_scores, [1, 2])
        self.assertEqual(recorded_scores, [1, 2])
        for _, queries in results:
            self.assertTrue(
                any("FOR UPDATE" in query.upper() for query in queries),
                queries,
            )
