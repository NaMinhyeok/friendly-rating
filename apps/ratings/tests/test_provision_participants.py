import re
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from ..models import Participant, PushDevice, RelationshipScore, ScoreChange
from ..participant_provisioning import (
    ProvisioningError,
    load_specs_from_environment,
)

PARTICIPANT_ENV = {
    "PARTICIPANT_1_NAME": "민수",
    "PARTICIPANT_1_PIN": "1234",
    "PARTICIPANT_2_NAME": "지수",
    "PARTICIPANT_2_PIN": "5678",
}
RECONCILED_ENV = {
    "PARTICIPANT_1_NAME": "민호",
    "PARTICIPANT_1_PIN": "4321",
    "PARTICIPANT_2_NAME": "지수",
    "PARTICIPANT_2_PIN": "5678",
}
DML_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|REPLACE)\b",
    re.IGNORECASE,
)


class ParticipantSpecificationTests(SimpleTestCase):
    def test_loads_valid_specs_without_database_access(self):
        specifications = load_specs_from_environment(PARTICIPANT_ENV)

        self.assertEqual(
            [
                (spec.slot, spec.username, spec.display_name, spec.pin)
                for spec in specifications
            ],
            [
                (1, "participant-1", "민수", "1234"),
                (2, "participant-2", "지수", "5678"),
            ],
        )

    def test_rejects_duplicate_display_names(self):
        duplicate_names = {**PARTICIPANT_ENV, "PARTICIPANT_2_NAME": "민수"}

        with self.assertRaisesMessage(ProvisioningError, "서로 달라야"):
            load_specs_from_environment(duplicate_names)

    def test_rejects_non_ascii_digits(self):
        non_ascii_pin = {**PARTICIPANT_ENV, "PARTICIPANT_1_PIN": "１２３４"}

        with self.assertRaisesMessage(ProvisioningError, "숫자 4자리"):
            load_specs_from_environment(non_ascii_pin)


class ProvisionParticipantsCommandTests(TestCase):
    def run_command(self, *arguments, environment=None):
        output = StringIO()
        with patch.dict(
            "os.environ",
            PARTICIPANT_ENV if environment is None else environment,
            clear=False,
        ):
            call_command("provision_participants", *arguments, stdout=output)
        return output.getvalue()

    def dml_queries(self, captured_queries):
        return [
            query["sql"]
            for query in captured_queries
            if DML_PATTERN.match(query["sql"])
        ]

    def aggregate_snapshot(self):
        user_model = get_user_model()
        return {
            "users": list(
                user_model.objects.filter(username__startswith="participant-")
                .order_by("pk")
                .values(
                    "pk",
                    "username",
                    "first_name",
                    "password",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                )
            ),
            "participants": list(
                Participant.objects.order_by("pk").values(
                    "pk", "user_id", "display_name", "slot", "created_at"
                )
            ),
            "scores": list(
                RelationshipScore.objects.order_by("pk").values(
                    "pk",
                    "source_participant_id",
                    "target_participant_id",
                    "current_score",
                    "updated_at",
                )
            ),
            "changes": list(
                ScoreChange.objects.order_by("pk").values(
                    "pk",
                    "relationship_score_id",
                    "changed_by_id",
                    "delta",
                    "reason",
                    "resulting_score",
                    "created_at",
                )
            ),
            "devices": list(
                PushDevice.objects.order_by("pk").values(
                    "pk",
                    "participant_id",
                    "firebase_installation_id",
                    "user_agent",
                    "is_active",
                    "created_at",
                    "updated_at",
                )
            ),
        }

    def add_score_history_and_device(self):
        first = Participant.objects.get(slot=Participant.Slot.FIRST)
        score = RelationshipScore.objects.get(source_participant=first)
        score.current_score = 7
        score.save(update_fields=("current_score", "updated_at"))
        ScoreChange.objects.create(
            relationship_score=score,
            changed_by=first,
            delta=7,
            reason="테스트 기록",
            resulting_score=7,
        )
        PushDevice.objects.create(
            participant=first,
            firebase_installation_id="c12345678901234567890A",
            user_agent="test agent",
        )

    def test_bootstrap_creates_complete_participant_aggregate(self):
        output = self.run_command()

        participants = list(Participant.objects.select_related("user"))
        scores = list(
            RelationshipScore.objects.order_by("source_participant__slot").values_list(
                "source_participant__slot",
                "target_participant__slot",
                "current_score",
            )
        )
        self.assertEqual([participant.slot for participant in participants], [1, 2])
        self.assertEqual(
            [participant.user.username for participant in participants],
            ["participant-1", "participant-2"],
        )
        self.assertEqual(
            [participant.display_name for participant in participants], ["민수", "지수"]
        )
        self.assertEqual(scores, [(1, 2, 0), (2, 1, 0)])
        self.assertTrue(participants[0].user.check_password("1234"))
        self.assertTrue(participants[1].user.check_password("5678"))
        for participant in participants:
            self.assertTrue(participant.user.is_active)
            self.assertFalse(participant.user.is_staff)
            self.assertFalse(participant.user.is_superuser)
        for secret in (*PARTICIPANT_ENV.values(),):
            self.assertNotIn(secret, output)

    def test_second_identical_run_performs_no_dml_and_preserves_state(self):
        self.run_command()
        self.add_score_history_and_device()
        before = self.aggregate_snapshot()

        with CaptureQueriesContext(connection) as queries:
            output = self.run_command()

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(self.aggregate_snapshot(), before)
        self.assertIn("변경하지 않았습니다", output)

    def test_check_accepts_matching_state_without_dml(self):
        self.run_command()

        with CaptureQueriesContext(connection) as queries:
            output = self.run_command("--check")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertIn("변경하지 않았습니다", output)

    def test_reconcile_matching_state_performs_no_dml(self):
        self.run_command()
        before = self.aggregate_snapshot()

        with CaptureQueriesContext(connection) as queries:
            output = self.run_command("--reconcile")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(self.aggregate_snapshot(), before)
        self.assertIn("변경하지 않았습니다", output)

    def test_check_rejects_empty_database_without_dml(self):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "비어 있습니다"):
                self.run_command("--check")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])

    def test_check_detects_legacy_hash_without_upgrading_it(self):
        self.run_command()
        user = get_user_model().objects.get(username="participant-1")
        legacy_hash = PBKDF2PasswordHasher().encode("1234", "legacy-salt", 1)
        get_user_model().objects.filter(pk=user.pk).update(password=legacy_hash)

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "PIN 해시 정책"):
                self.run_command("--check")

        user.refresh_from_db()
        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(user.password, legacy_hash)

    def test_default_mode_rejects_drift_without_dml(self):
        self.run_command()
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        get_user_model().objects.filter(pk=participant.user_id).update(is_staff=True)

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "변경하지 않았습니다"):
                self.run_command()

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertTrue(get_user_model().objects.get(pk=participant.user_id).is_staff)

    def test_reconcile_rejects_partial_graph_without_dml(self):
        user = get_user_model().objects.create_user(
            username="participant-1",
            password="1234",
        )
        Participant.objects.create(
            user=user,
            display_name="민수",
            slot=Participant.Slot.FIRST,
        )
        before = self.aggregate_snapshot()

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "소유 관계가 불완전"):
                self.run_command("--reconcile")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_reconcile_updates_name_pin_and_flags_while_preserving_related_data(self):
        self.run_command()
        self.add_score_history_and_device()
        first = Participant.objects.get(slot=Participant.Slot.FIRST)
        first_user_id = first.user_id
        get_user_model().objects.filter(pk=first_user_id).update(
            is_active=False,
            is_staff=True,
            is_superuser=True,
        )
        score_snapshot = list(
            RelationshipScore.objects.order_by("pk").values(
                "pk", "current_score", "updated_at"
            )
        )
        history_snapshot = list(ScoreChange.objects.order_by("pk").values())
        device_snapshot = list(PushDevice.objects.order_by("pk").values())

        with CaptureQueriesContext(connection) as queries:
            output = self.run_command("--reconcile", environment=RECONCILED_ENV)

        first.refresh_from_db()
        user = get_user_model().objects.get(pk=first_user_id)
        self.assertEqual(first.display_name, "민호")
        self.assertEqual(user.first_name, "민호")
        self.assertTrue(user.check_password("4321"))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(
            list(
                RelationshipScore.objects.order_by("pk").values(
                    "pk", "current_score", "updated_at"
                )
            ),
            score_snapshot,
        )
        self.assertEqual(
            list(ScoreChange.objects.order_by("pk").values()), history_snapshot
        )
        self.assertEqual(
            list(PushDevice.objects.order_by("pk").values()), device_snapshot
        )
        relationship_writes = [
            sql
            for sql in self.dml_queries(queries.captured_queries)
            if '"relationship_score"' in sql.lower()
        ]
        self.assertEqual(relationship_writes, [])
        self.assertIn("안전하게 반영", output)
        for secret in (*RECONCILED_ENV.values(),):
            self.assertNotIn(secret, output)

    def test_reconcile_can_swap_display_names(self):
        self.run_command()
        swapped_environment = {
            **PARTICIPANT_ENV,
            "PARTICIPANT_1_NAME": "지수",
            "PARTICIPANT_2_NAME": "민수",
        }

        self.run_command("--reconcile", environment=swapped_environment)

        self.assertEqual(
            list(Participant.objects.values_list("display_name", flat=True)),
            ["지수", "민수"],
        )

    def test_reconcile_creates_only_a_missing_directional_score(self):
        self.run_command()
        self.add_score_history_and_device()
        existing = RelationshipScore.objects.get(
            source_participant__slot=Participant.Slot.FIRST
        )
        missing = RelationshipScore.objects.get(
            source_participant__slot=Participant.Slot.SECOND
        )
        missing.delete()
        existing_snapshot = (existing.pk, existing.current_score, existing.updated_at)
        history_snapshot = list(ScoreChange.objects.order_by("pk").values())
        device_snapshot = list(PushDevice.objects.order_by("pk").values())

        with CaptureQueriesContext(connection) as queries:
            self.run_command("--reconcile")

        existing.refresh_from_db()
        self.assertEqual(
            (existing.pk, existing.current_score, existing.updated_at),
            existing_snapshot,
        )
        self.assertEqual(RelationshipScore.objects.count(), 2)
        self.assertEqual(
            list(ScoreChange.objects.order_by("pk").values()), history_snapshot
        )
        self.assertEqual(
            list(PushDevice.objects.order_by("pk").values()), device_snapshot
        )
        self.assertEqual(len(self.dml_queries(queries.captured_queries)), 1)
        self.assertIn(
            'INSERT INTO "relationship_score"',
            self.dml_queries(queries.captured_queries)[0],
        )

    def test_reconcile_rolls_back_all_changes_when_second_name_update_fails(self):
        self.run_command()
        changed_environment = {
            "PARTICIPANT_1_NAME": "민호",
            "PARTICIPANT_1_PIN": "4321",
            "PARTICIPANT_2_NAME": "지윤",
            "PARTICIPANT_2_PIN": "8765",
        }
        before = self.aggregate_snapshot()
        original_save = Participant.save

        def fail_on_second_final_name(instance, *args, **kwargs):
            if (
                instance.slot == Participant.Slot.SECOND
                and instance.display_name == "지윤"
            ):
                raise RuntimeError("injected reconcile failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(Participant, "save", new=fail_on_second_final_name):
            with self.assertRaisesMessage(RuntimeError, "injected reconcile failure"):
                self.run_command("--reconcile", environment=changed_environment)

        self.assertEqual(self.aggregate_snapshot(), before)

    def test_reconcile_rejects_canonical_username_collision_without_writes(self):
        self.run_command()
        first = Participant.objects.select_related("user").get(
            slot=Participant.Slot.FIRST
        )
        first.user.username = "legacy-participant-1"
        first.user.save(update_fields=["username"])
        get_user_model().objects.create_user(username="participant-1")
        before = self.aggregate_snapshot()

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "예약 사용자 이름"):
                self.run_command("--reconcile")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_rejects_invalid_pin_before_querying_database(self):
        invalid_environment = {**PARTICIPANT_ENV, "PARTICIPANT_1_PIN": "12ab"}

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "숫자 4자리"):
                self.run_command(environment=invalid_environment)

        self.assertEqual(queries.captured_queries, [])

    def test_check_and_reconcile_are_mutually_exclusive(self):
        with self.assertRaises(CommandError):
            self.run_command("--check", "--reconcile")
