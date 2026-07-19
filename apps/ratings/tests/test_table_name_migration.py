from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DomainTableRenameMigrationTests(TransactionTestCase):
    migrate_from = [("ratings", "0005_rename_relationship_and_push_fields")]
    migrate_to = [("ratings", "0006_rename_domain_tables")]
    old_table_names = {
        "ratings_participant",
        "ratings_relationshipscore",
        "ratings_scorechange",
        "ratings_pushdevice",
    }
    new_table_names = {
        "participant",
        "relationship_score",
        "score_change",
        "push_device",
    }

    def setUp(self):
        super().setUp()
        self.addCleanup(self._migrate_to_latest)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.legacy_ids = self._create_legacy_data(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.migrated_apps = executor.loader.project_state(self.migrate_to).apps

    def _migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _create_legacy_data(self, apps):
        User = apps.get_model("auth", "User")
        Participant = apps.get_model("ratings", "Participant")
        PushDevice = apps.get_model("ratings", "PushDevice")
        RelationshipScore = apps.get_model("ratings", "RelationshipScore")
        ScoreChange = apps.get_model("ratings", "ScoreChange")

        first_user = User.objects.create(username="table-migration-participant-1")
        second_user = User.objects.create(username="table-migration-participant-2")
        first = Participant.objects.create(
            user=first_user,
            display_name="테이블 마이그레이션 첫 번째",
            slot=1,
        )
        second = Participant.objects.create(
            user=second_user,
            display_name="테이블 마이그레이션 두 번째",
            slot=2,
        )
        relationship_score = RelationshipScore.objects.create(
            source_participant=first,
            target_participant=second,
            current_score=42,
        )
        score_change = ScoreChange.objects.create(
            relationship_score=relationship_score,
            changed_by=first,
            delta=42,
            reason="테이블 이름 변경 전 데이터",
            resulting_score=42,
        )
        push_device = PushDevice.objects.create(
            participant=second,
            firebase_installation_id="e12345678901234567890C",
            is_active=False,
            user_agent="table-migration-test",
        )
        return {
            "first_id": first.pk,
            "second_id": second.pk,
            "relationship_score_id": relationship_score.pk,
            "score_change_id": score_change.pk,
            "push_device_id": push_device.pk,
        }

    def test_migration_uses_explicit_snake_case_table_names(self):
        expected_model_tables = {
            "Participant": "participant",
            "RelationshipScore": "relationship_score",
            "ScoreChange": "score_change",
            "PushDevice": "push_device",
        }

        for model_name, table_name in expected_model_tables.items():
            model = self.migrated_apps.get_model("ratings", model_name)
            self.assertEqual(model._meta.db_table, table_name)

        table_names = set(connection.introspection.table_names())
        self.assertTrue(self.new_table_names <= table_names)
        self.assertTrue(self.old_table_names.isdisjoint(table_names))

    def test_migration_preserves_rows_and_relationships(self):
        Participant = self.migrated_apps.get_model("ratings", "Participant")
        PushDevice = self.migrated_apps.get_model("ratings", "PushDevice")
        RelationshipScore = self.migrated_apps.get_model("ratings", "RelationshipScore")
        ScoreChange = self.migrated_apps.get_model("ratings", "ScoreChange")

        first = Participant.objects.get(pk=self.legacy_ids["first_id"])
        second = Participant.objects.get(pk=self.legacy_ids["second_id"])
        relationship_score = RelationshipScore.objects.get(
            pk=self.legacy_ids["relationship_score_id"]
        )
        score_change = ScoreChange.objects.get(pk=self.legacy_ids["score_change_id"])
        push_device = PushDevice.objects.get(pk=self.legacy_ids["push_device_id"])

        self.assertEqual(relationship_score.source_participant_id, first.pk)
        self.assertEqual(relationship_score.target_participant_id, second.pk)
        self.assertEqual(relationship_score.current_score, 42)
        self.assertEqual(score_change.relationship_score_id, relationship_score.pk)
        self.assertEqual(score_change.changed_by_id, first.pk)
        self.assertEqual(score_change.resulting_score, 42)
        self.assertEqual(push_device.participant_id, second.pk)
        self.assertEqual(
            push_device.firebase_installation_id,
            "e12345678901234567890C",
        )
        self.assertFalse(push_device.is_active)

        reverse_score = RelationshipScore.objects.create(
            source_participant=second,
            target_participant=first,
            current_score=5,
        )
        reverse_change = ScoreChange.objects.create(
            relationship_score=reverse_score,
            changed_by=second,
            delta=5,
            reason="테이블 이름 변경 후 데이터",
            resulting_score=5,
        )
        new_device = PushDevice.objects.create(
            participant=first,
            firebase_installation_id="f12345678901234567890D",
            user_agent="post-table-migration-test",
        )
        self.assertGreater(reverse_score.pk, relationship_score.pk)
        self.assertGreater(reverse_change.pk, score_change.pk)
        self.assertGreater(new_device.pk, push_device.pk)

    def test_constraints_and_indexes_remain_available_after_table_rename(self):
        with connection.cursor() as cursor:
            relationship_constraints = connection.introspection.get_constraints(
                cursor, "relationship_score"
            )
            score_change_constraints = connection.introspection.get_constraints(
                cursor, "score_change"
            )
            push_device_constraints = connection.introspection.get_constraints(
                cursor, "push_device"
            )

        self.assertTrue(
            relationship_constraints["relationship_score_between_0_and_100"]["check"]
        )
        self.assertTrue(
            relationship_constraints[
                "relationship_score_between_different_participants"
            ]["check"]
        )
        self.assertTrue(
            score_change_constraints["score_change_delta_is_not_zero"]["check"]
        )
        self.assertTrue(
            score_change_constraints["score_change_result_between_0_and_100"]["check"]
        )
        self.assertTrue(
            any(
                details["index"] and details["columns"] == ["is_active"]
                for details in push_device_constraints.values()
            )
        )

    def test_reverse_migration_restores_default_table_names_and_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        table_names = set(connection.introspection.table_names())
        self.assertTrue(self.old_table_names <= table_names)
        self.assertTrue(self.new_table_names.isdisjoint(table_names))

        Participant = old_apps.get_model("ratings", "Participant")
        PushDevice = old_apps.get_model("ratings", "PushDevice")
        RelationshipScore = old_apps.get_model("ratings", "RelationshipScore")
        ScoreChange = old_apps.get_model("ratings", "ScoreChange")
        first = Participant.objects.get(pk=self.legacy_ids["first_id"])
        relationship_score = RelationshipScore.objects.get(
            pk=self.legacy_ids["relationship_score_id"]
        )
        score_change = ScoreChange.objects.get(pk=self.legacy_ids["score_change_id"])
        push_device = PushDevice.objects.get(pk=self.legacy_ids["push_device_id"])
        self.assertEqual(relationship_score.source_participant_id, first.pk)
        self.assertEqual(relationship_score.current_score, 42)
        self.assertEqual(score_change.relationship_score_id, relationship_score.pk)
        self.assertEqual(push_device.participant_id, self.legacy_ids["second_id"])

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.migrated_apps = executor.loader.project_state(self.migrate_to).apps
        table_names = set(connection.introspection.table_names())
        self.assertTrue(self.new_table_names <= table_names)
        self.assertTrue(self.old_table_names.isdisjoint(table_names))
        ScoreChange = self.migrated_apps.get_model("ratings", "ScoreChange")
        self.assertTrue(
            ScoreChange.objects.filter(pk=self.legacy_ids["score_change_id"]).exists()
        )
