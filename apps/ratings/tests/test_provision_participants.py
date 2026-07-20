import re
from datetime import timedelta
from io import StringIO
from typing import cast
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..models import (
    DiaryEntry,
    DiaryEntryComment,
    MediaAttachment,
    Participant,
    PushDevice,
    RelationshipScore,
    ScoreChange,
    ScoreChangeComment,
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


def get_concrete_user_model() -> type[User]:
    return cast(type[User], get_user_model())


def run_provision_command(*arguments, environment=None):
    output = StringIO()
    with patch.dict(
        "os.environ",
        PARTICIPANT_ENV if environment is None else environment,
        clear=False,
    ):
        call_command("provision_participants", *arguments, stdout=output)
    return output.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("environment", "error_message"),
    (
        pytest.param(
            {**PARTICIPANT_ENV, "PARTICIPANT_2_NAME": "민수"},
            "서로 달라야",
            id="duplicate-display-names",
        ),
        pytest.param(
            {**PARTICIPANT_ENV, "PARTICIPANT_1_PIN": "１２３４"},
            "숫자 4자리",
            id="non-ascii-digits",
        ),
        pytest.param(
            {**PARTICIPANT_ENV, "PARTICIPANT_1_PIN": "12ab"},
            "숫자 4자리",
            id="non-digit-pin",
        ),
    ),
)
def test_invalid_participant_configuration_does_not_change_database(
    environment,
    error_message,
):
    with pytest.raises(CommandError, match=error_message):
        run_provision_command(environment=environment)

    assert (
        not get_concrete_user_model()
        .objects.filter(username__startswith="participant-")
        .exists()
    )
    assert not Participant.objects.exists()
    assert not RelationshipScore.objects.exists()


class ProvisionParticipantsCommandTests(TestCase):
    def run_command(self, *arguments, environment=None):
        return run_provision_command(*arguments, environment=environment)

    def dml_queries(self, captured_queries):
        return [
            query["sql"]
            for query in captured_queries
            if DML_PATTERN.match(query["sql"])
        ]

    def aggregate_snapshot(self):
        user_model = get_concrete_user_model()
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
            "comments": list(
                ScoreChangeComment.objects.order_by("pk").values(
                    "pk",
                    "score_change_id",
                    "author_id",
                    "content",
                    "media_count",
                    "created_at",
                )
            ),
            "diary_entries": list(
                DiaryEntry.objects.order_by("pk").values(
                    "pk",
                    "author_id",
                    "content",
                    "created_at",
                    "updated_at",
                )
            ),
            "diary_entry_comments": list(
                DiaryEntryComment.objects.order_by("pk").values(
                    "pk",
                    "diary_entry_id",
                    "author_id",
                    "content",
                    "created_at",
                )
            ),
            "media_attachments": list(
                MediaAttachment.objects.order_by("created_at", "pk").values(
                    "pk",
                    "uploader_id",
                    "score_change_id",
                    "comment_id",
                    "diary_entry_id",
                    "purpose",
                    "kind",
                    "status",
                    "object_key",
                    "original_name",
                    "content_type",
                    "expected_size",
                    "actual_size",
                    "etag",
                    "expires_at",
                    "created_at",
                    "finalized_at",
                    "finalization_token",
                    "position",
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
        change = ScoreChange.objects.create(
            relationship_score=score,
            changed_by=first,
            delta=7,
            reason="테스트 기록",
            resulting_score=7,
        )
        second = Participant.objects.get(slot=Participant.Slot.SECOND)
        diary_entry = DiaryEntry.objects.create(
            author=first,
            content="보존할 공유 일기",
        )
        comment = ScoreChangeComment.objects.create(
            score_change=change,
            author=second,
            content="보존할 댓글",
            media_count=1,
        )
        DiaryEntryComment.objects.create(
            diary_entry=diary_entry,
            author=second,
            content="보존할 일기 댓글",
        )
        finalized_at = timezone.now()
        MediaAttachment.objects.create(
            uploader=first,
            score_change=change,
            purpose=MediaAttachment.Purpose.SCORE_CHANGE,
            kind=MediaAttachment.Kind.IMAGE,
            status=MediaAttachment.Status.ATTACHED,
            object_key="media/provision-score-image",
            original_name="점수사진.webp",
            content_type="image/webp",
            expected_size=1_024,
            actual_size=1_024,
            etag="score-image-etag",
            expires_at=finalized_at + timedelta(hours=1),
            finalized_at=finalized_at,
            position=0,
        )
        MediaAttachment.objects.create(
            uploader=second,
            score_change=change,
            comment=comment,
            purpose=MediaAttachment.Purpose.COMMENT,
            kind=MediaAttachment.Kind.VIDEO,
            status=MediaAttachment.Status.ATTACHED,
            object_key="media/provision-comment-video",
            original_name="댓글영상.mp4",
            content_type="video/mp4",
            expected_size=4_096,
            actual_size=4_096,
            etag="comment-video-etag",
            expires_at=finalized_at + timedelta(hours=1),
            finalized_at=finalized_at,
            position=0,
        )
        MediaAttachment.objects.create(
            uploader=first,
            diary_entry=diary_entry,
            purpose=MediaAttachment.Purpose.DIARY_ENTRY,
            kind=MediaAttachment.Kind.IMAGE,
            status=MediaAttachment.Status.ATTACHED,
            object_key="media/provision-diary-image",
            original_name="일기사진.webp",
            content_type="image/webp",
            expected_size=2_048,
            actual_size=2_048,
            etag="diary-image-etag",
            expires_at=finalized_at + timedelta(hours=1),
            finalized_at=finalized_at,
            position=0,
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
        self.assertNotIn(PARTICIPANT_ENV["PARTICIPANT_1_PIN"], output)
        self.assertNotIn(PARTICIPANT_ENV["PARTICIPANT_2_PIN"], output)

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

    def test_check_rejects_empty_database_without_dml(self):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "비어 있습니다"):
                self.run_command("--check")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])

    def test_check_detects_legacy_hash_without_upgrading_it(self):
        self.run_command()
        user = get_concrete_user_model().objects.get(username="participant-1")
        legacy_hash = PBKDF2PasswordHasher().encode("1234", "legacy-salt", 1)
        get_concrete_user_model().objects.filter(pk=user.pk).update(
            password=legacy_hash
        )

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "PIN 해시 정책"):
                self.run_command("--check")

        user.refresh_from_db()
        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(user.password, legacy_hash)

    def test_default_mode_rejects_drift_without_dml(self):
        self.run_command()
        participant = Participant.objects.get(slot=Participant.Slot.FIRST)
        get_concrete_user_model().objects.filter(pk=participant.user_id).update(
            is_staff=True
        )

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "변경하지 않았습니다"):
                self.run_command()

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertTrue(
            get_concrete_user_model().objects.get(pk=participant.user_id).is_staff
        )

    def test_reconcile_rejects_partial_graph_without_dml(self):
        user = get_concrete_user_model().objects.create_user(
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
        get_concrete_user_model().objects.filter(pk=first_user_id).update(
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
        comment_snapshot = list(ScoreChangeComment.objects.order_by("pk").values())
        diary_snapshot = list(DiaryEntry.objects.order_by("pk").values())
        diary_comment_snapshot = list(DiaryEntryComment.objects.order_by("pk").values())
        media_snapshot = list(MediaAttachment.objects.order_by("pk").values())
        device_snapshot = list(PushDevice.objects.order_by("pk").values())

        output = self.run_command("--reconcile", environment=RECONCILED_ENV)

        first.refresh_from_db()
        user = get_concrete_user_model().objects.get(pk=first_user_id)
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
            list(ScoreChangeComment.objects.order_by("pk").values()),
            comment_snapshot,
        )
        self.assertEqual(
            list(DiaryEntry.objects.order_by("pk").values()), diary_snapshot
        )
        self.assertEqual(
            list(DiaryEntryComment.objects.order_by("pk").values()),
            diary_comment_snapshot,
        )
        self.assertEqual(
            list(MediaAttachment.objects.order_by("pk").values()),
            media_snapshot,
        )
        self.assertEqual(
            list(PushDevice.objects.order_by("pk").values()), device_snapshot
        )
        self.assertIn("안전하게 반영", output)
        self.assertNotIn(RECONCILED_ENV["PARTICIPANT_1_PIN"], output)
        self.assertNotIn(RECONCILED_ENV["PARTICIPANT_2_PIN"], output)

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
        comment_snapshot = list(ScoreChangeComment.objects.order_by("pk").values())
        diary_snapshot = list(DiaryEntry.objects.order_by("pk").values())
        diary_comment_snapshot = list(DiaryEntryComment.objects.order_by("pk").values())
        device_snapshot = list(PushDevice.objects.order_by("pk").values())

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
            list(ScoreChangeComment.objects.order_by("pk").values()),
            comment_snapshot,
        )
        self.assertEqual(
            list(DiaryEntry.objects.order_by("pk").values()), diary_snapshot
        )
        self.assertEqual(
            list(DiaryEntryComment.objects.order_by("pk").values()),
            diary_comment_snapshot,
        )
        self.assertEqual(
            list(PushDevice.objects.order_by("pk").values()), device_snapshot
        )
        recreated = RelationshipScore.objects.get(
            source_participant__slot=Participant.Slot.SECOND
        )
        self.assertEqual(recreated.target_participant.slot, Participant.Slot.FIRST)
        self.assertEqual(recreated.current_score, 0)

    def test_reconcile_rolls_back_all_changes_when_reconciliation_fails(self):
        self.run_command()
        changed_environment = {
            "PARTICIPANT_1_NAME": "민호",
            "PARTICIPANT_1_PIN": "4321",
            "PARTICIPANT_2_NAME": "지윤",
            "PARTICIPANT_2_PIN": "8765",
        }
        before = self.aggregate_snapshot()

        def change_one_participant_then_fail(_specifications, snapshot):
            participant = snapshot.participants_by_slot[Participant.Slot.FIRST]
            participant.display_name = "원자성 검증 중 변경"
            participant.save(update_fields=["display_name"])
            raise RuntimeError("injected reconcile failure")

        with patch(
            "apps.ratings.participant_provisioning.service.reconcile_participants",
            new=change_one_participant_then_fail,
        ):
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
        get_concrete_user_model().objects.create_user(username="participant-1")
        before = self.aggregate_snapshot()

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, "예약 사용자 이름"):
                self.run_command("--reconcile")

        self.assertEqual(self.dml_queries(queries.captured_queries), [])
        self.assertEqual(self.aggregate_snapshot(), before)
