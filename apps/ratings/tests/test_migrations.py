from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RelationshipAndPushFieldRenameMigrationTests(TransactionTestCase):
    migrate_from = [("ratings", "0004_pushdevice")]
    migrate_to = [("ratings", "0005_rename_relationship_and_push_fields")]

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

        first_user = User.objects.create(username="migration-participant-1")
        second_user = User.objects.create(username="migration-participant-2")
        first = Participant.objects.create(
            user=first_user,
            display_name="마이그레이션 첫 번째",
            slot=1,
        )
        second = Participant.objects.create(
            user=second_user,
            display_name="마이그레이션 두 번째",
            slot=2,
        )
        relationship_score = RelationshipScore.objects.create(
            rater=first,
            recipient=second,
            value=42,
        )
        score_change = ScoreChange.objects.create(
            score=relationship_score,
            changed_by=first,
            delta=42,
            reason="기존 데이터",
            resulting_score=42,
        )
        push_device = PushDevice.objects.create(
            participant=second,
            fid="c12345678901234567890A",
            active=False,
            user_agent="migration-test",
        )
        return {
            "first_id": first.pk,
            "second_id": second.pk,
            "relationship_score_id": relationship_score.pk,
            "score_change_id": score_change.pk,
            "push_device_id": push_device.pk,
        }

    def test_renamed_fields_preserve_existing_data_and_relationships(self):
        PushDevice = self.migrated_apps.get_model("ratings", "PushDevice")
        RelationshipScore = self.migrated_apps.get_model("ratings", "RelationshipScore")
        ScoreChange = self.migrated_apps.get_model("ratings", "ScoreChange")

        relationship_score = RelationshipScore.objects.get(
            pk=self.legacy_ids["relationship_score_id"]
        )
        score_change = ScoreChange.objects.get(pk=self.legacy_ids["score_change_id"])
        push_device = PushDevice.objects.get(pk=self.legacy_ids["push_device_id"])

        self.assertEqual(
            relationship_score.source_participant_id,
            self.legacy_ids["first_id"],
        )
        self.assertEqual(
            relationship_score.target_participant_id,
            self.legacy_ids["second_id"],
        )
        self.assertEqual(relationship_score.current_score, 42)
        self.assertEqual(
            score_change.relationship_score_id,
            relationship_score.pk,
        )
        self.assertEqual(score_change.resulting_score, 42)
        self.assertEqual(
            push_device.firebase_installation_id,
            "c12345678901234567890A",
        )
        self.assertFalse(push_device.is_active)

    def test_database_uses_the_new_column_names(self):
        expected_columns = {
            "ratings_relationshipscore": {
                "source_participant_id",
                "target_participant_id",
                "current_score",
            },
            "ratings_scorechange": {"relationship_score_id"},
            "ratings_pushdevice": {"firebase_installation_id", "is_active"},
        }
        removed_columns = {
            "ratings_relationshipscore": {"rater_id", "recipient_id", "value"},
            "ratings_scorechange": {"score_id"},
            "ratings_pushdevice": {"fid", "active"},
        }

        with connection.cursor() as cursor:
            for table_name, expected in expected_columns.items():
                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        cursor, table_name
                    )
                }
                self.assertTrue(expected <= columns)
                self.assertTrue(columns.isdisjoint(removed_columns[table_name]))

    def test_constraints_and_index_follow_the_renamed_columns(self):
        with connection.cursor() as cursor:
            relationship_constraints = connection.introspection.get_constraints(
                cursor, "ratings_relationshipscore"
            )
            push_device_constraints = connection.introspection.get_constraints(
                cursor, "ratings_pushdevice"
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
            any(
                details["unique"] and details["columns"] == ["source_participant_id"]
                for details in relationship_constraints.values()
            )
        )
        self.assertTrue(
            any(
                details["unique"] and details["columns"] == ["target_participant_id"]
                for details in relationship_constraints.values()
            )
        )
        self.assertTrue(
            any(
                details["unique"] and details["columns"] == ["firebase_installation_id"]
                for details in push_device_constraints.values()
            )
        )
        self.assertTrue(
            any(
                details["index"] and details["columns"] == ["is_active"]
                for details in push_device_constraints.values()
            )
        )

    def test_reverse_migration_restores_old_names_and_preserves_data(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        PushDevice = old_apps.get_model("ratings", "PushDevice")
        RelationshipScore = old_apps.get_model("ratings", "RelationshipScore")
        ScoreChange = old_apps.get_model("ratings", "ScoreChange")

        relationship_score = RelationshipScore.objects.get(
            pk=self.legacy_ids["relationship_score_id"]
        )
        score_change = ScoreChange.objects.get(pk=self.legacy_ids["score_change_id"])
        push_device = PushDevice.objects.get(pk=self.legacy_ids["push_device_id"])

        self.assertEqual(relationship_score.rater_id, self.legacy_ids["first_id"])
        self.assertEqual(
            relationship_score.recipient_id,
            self.legacy_ids["second_id"],
        )
        self.assertEqual(relationship_score.value, 42)
        self.assertEqual(score_change.score_id, relationship_score.pk)
        self.assertEqual(push_device.fid, "c12345678901234567890A")
        self.assertFalse(push_device.active)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.migrated_apps = executor.loader.project_state(self.migrate_to).apps
