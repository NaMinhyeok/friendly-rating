from datetime import timedelta

import pytest
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_diary_media_migration_preserves_existing_entries_and_media():
    executor = MigrationExecutor(connection)
    old_target = [("ratings", "0009_diaryentry")]
    new_target = [("ratings", "0010_diary_entry_media_attachments")]
    executor.migrate(old_target)
    old_apps = executor.loader.project_state(old_target).apps

    app_label, model_name = settings.AUTH_USER_MODEL.split(".", 1)
    User = old_apps.get_model(app_label, model_name)
    Participant = old_apps.get_model("ratings", "Participant")
    RelationshipScore = old_apps.get_model("ratings", "RelationshipScore")
    ScoreChange = old_apps.get_model("ratings", "ScoreChange")
    ScoreChangeComment = old_apps.get_model("ratings", "ScoreChangeComment")
    DiaryEntry = old_apps.get_model("ratings", "DiaryEntry")
    MediaAttachment = old_apps.get_model("ratings", "MediaAttachment")

    first_user = User.objects.create(username="migration-first")
    second_user = User.objects.create(username="migration-second")
    first = Participant.objects.create(
        user_id=first_user.pk,
        display_name="이전 첫 번째",
        slot=1,
    )
    second = Participant.objects.create(
        user_id=second_user.pk,
        display_name="이전 두 번째",
        slot=2,
    )
    relationship = RelationshipScore.objects.create(
        source_participant_id=first.pk,
        target_participant_id=second.pk,
        current_score=3,
    )
    change = ScoreChange.objects.create(
        relationship_score_id=relationship.pk,
        changed_by_id=first.pk,
        delta=3,
        reason="기존 기록",
        resulting_score=3,
    )
    comment = ScoreChangeComment.objects.create(
        score_change_id=change.pk,
        author_id=second.pk,
        content="기존 댓글",
        media_count=1,
    )
    diary_entry = DiaryEntry.objects.create(
        author_id=first.pk,
        content="기존 공유 일기",
    )
    finalized_at = timezone.now()
    score_attachment = MediaAttachment.objects.create(
        uploader_id=first.pk,
        score_change_id=change.pk,
        purpose="score_change",
        kind="image",
        status="attached",
        object_key="media/migration-score",
        original_name="score.webp",
        content_type="image/webp",
        expected_size=1_024,
        actual_size=1_024,
        expires_at=finalized_at + timedelta(hours=1),
        finalized_at=finalized_at,
    )
    comment_attachment = MediaAttachment.objects.create(
        uploader_id=second.pk,
        score_change_id=change.pk,
        comment_id=comment.pk,
        purpose="comment",
        kind="video",
        status="attached",
        object_key="media/migration-comment",
        original_name="comment.mp4",
        content_type="video/mp4",
        expected_size=4_096,
        actual_size=4_096,
        expires_at=finalized_at + timedelta(hours=1),
        finalized_at=finalized_at,
    )

    executor = MigrationExecutor(connection)
    executor.migrate(new_target)
    new_apps = executor.loader.project_state(new_target).apps
    NewDiaryEntry = new_apps.get_model("ratings", "DiaryEntry")
    NewMediaAttachment = new_apps.get_model("ratings", "MediaAttachment")

    assert NewDiaryEntry.objects.get(pk=diary_entry.pk).content == "기존 공유 일기"
    migrated_score = NewMediaAttachment.objects.get(pk=score_attachment.pk)
    migrated_comment = NewMediaAttachment.objects.get(pk=comment_attachment.pk)
    assert (
        migrated_score.score_change_id,
        migrated_score.comment_id,
        migrated_score.diary_entry_id,
        migrated_score.object_key,
    ) == (change.pk, None, None, "media/migration-score")
    assert (
        migrated_comment.score_change_id,
        migrated_comment.comment_id,
        migrated_comment.diary_entry_id,
        migrated_comment.object_key,
    ) == (change.pk, comment.pk, None, "media/migration-comment")

    diary_attachment = NewMediaAttachment.objects.create(
        uploader_id=first.pk,
        diary_entry_id=diary_entry.pk,
        purpose="diary_entry",
        kind="image",
        status="attached",
        object_key="media/migration-diary",
        original_name="diary.webp",
        content_type="image/webp",
        expected_size=2_048,
        actual_size=2_048,
        expires_at=finalized_at + timedelta(hours=1),
        finalized_at=finalized_at,
    )
    assert diary_attachment.diary_entry_id == diary_entry.pk
