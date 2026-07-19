import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Participant(models.Model):
    user_id: int

    class Slot(models.IntegerChoices):
        FIRST = 1, "첫 번째"
        SECOND = 2, "두 번째"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="participant",
    )
    display_name = models.CharField(max_length=30, unique=True)
    slot = models.PositiveSmallIntegerField(choices=Slot, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "participant"
        ordering = ("slot",)
        constraints = [
            models.CheckConstraint(
                condition=Q(slot__in=(1, 2)),
                name="participant_slot_is_first_or_second",
            ),
        ]

    def __str__(self):
        return self.display_name


class DiaryEntry(models.Model):
    author = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        related_name="diary_entries",
    )
    content = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "diary_entry"
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=~Q(content__regex=r"^\s*$"),
                name="diary_entry_content_not_blank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.author} · {self.created_at}"


class RelationshipScore(models.Model):
    source_participant = models.OneToOneField(
        Participant,
        on_delete=models.PROTECT,
        related_name="outgoing_score",
    )
    target_participant = models.OneToOneField(
        Participant,
        on_delete=models.PROTECT,
        related_name="incoming_score",
    )
    current_score = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "relationship_score"
        constraints = [
            models.CheckConstraint(
                condition=Q(current_score__gte=0) & Q(current_score__lte=100),
                name="relationship_score_between_0_and_100",
            ),
            models.CheckConstraint(
                condition=~Q(source_participant=F("target_participant")),
                name="relationship_score_between_different_participants",
            ),
        ]

    def __str__(self):
        return (
            f"{self.source_participant} → {self.target_participant}: "
            f"{self.current_score}"
        )


class PushDevice(models.Model):
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="push_devices",
    )
    firebase_installation_id = models.CharField(max_length=255, unique=True)
    user_agent = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "push_device"
        ordering = ("participant__slot", "-updated_at")

    def __str__(self):
        state = "활성" if self.is_active else "비활성"
        return f"{self.participant} 기기 ({state})"


class ImmutableScoreChangeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("점수 변경 기록은 수정할 수 없습니다.")

    def delete(self):
        raise ValidationError("점수 변경 기록은 삭제할 수 없습니다.")


class ScoreChange(models.Model):
    relationship_score = models.ForeignKey(
        RelationshipScore,
        on_delete=models.PROTECT,
        related_name="changes",
    )
    changed_by = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        related_name="score_changes",
    )
    delta = models.SmallIntegerField()
    reason = models.CharField(max_length=200, blank=True)
    resulting_score = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ImmutableScoreChangeQuerySet.as_manager()

    class Meta:
        db_table = "score_change"
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=~Q(delta=0),
                name="score_change_delta_is_not_zero",
            ),
            models.CheckConstraint(
                condition=Q(resulting_score__gte=0) & Q(resulting_score__lte=100),
                name="score_change_result_between_0_and_100",
            ),
        ]

    def __str__(self):
        sign = "+" if self.delta > 0 else ""
        return f"{self.changed_by}: {sign}{self.delta} → {self.resulting_score}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("점수 변경 기록은 수정할 수 없습니다.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("점수 변경 기록은 삭제할 수 없습니다.")


class ScoreChangeComment(models.Model):
    score_change = models.ForeignKey(
        ScoreChange,
        on_delete=models.PROTECT,
        related_name="comments",
    )
    author = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        related_name="score_change_comments",
    )
    content = models.CharField(max_length=500, blank=True)
    media_count = models.PositiveSmallIntegerField(default=0, db_default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "score_change_comment"
        ordering = ("created_at", "pk")
        constraints = [
            models.CheckConstraint(
                condition=~Q(content="") | Q(media_count__gt=0),
                name="score_comment_has_content_or_media",
            ),
            models.CheckConstraint(
                condition=Q(media_count__lte=4),
                name="score_comment_media_count_at_most_4",
            ),
        ]
        indexes = [
            models.Index(
                fields=("score_change", "created_at", "id"),
                name="score_comment_thread_order_idx",
            ),
        ]

    def __str__(self):
        return f"{self.author}: {self.content[:30]}"


class MediaAttachment(models.Model):
    class Purpose(models.TextChoices):
        SCORE_CHANGE = "score_change", "점수 변경"
        COMMENT = "comment", "댓글"
        DIARY_ENTRY = "diary_entry", "공유 일기"

    class Kind(models.TextChoices):
        IMAGE = "image", "이미지"
        VIDEO = "video", "영상"

    class Status(models.TextChoices):
        PENDING = "pending", "업로드 대기"
        FINALIZING = "finalizing", "업로드 확인 중"
        RECLAIMING = "reclaiming", "이전 확인 작업 정리 중"
        READY = "ready", "연결 대기"
        DELETING = "deleting", "만료 업로드 삭제 중"
        ATTACHED = "attached", "연결 완료"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploader = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        related_name="media_attachments",
    )
    score_change = models.ForeignKey(
        ScoreChange,
        on_delete=models.PROTECT,
        related_name="media_attachments",
        null=True,
        blank=True,
    )
    comment = models.ForeignKey(
        ScoreChangeComment,
        on_delete=models.PROTECT,
        related_name="media_attachments",
        null=True,
        blank=True,
    )
    diary_entry = models.ForeignKey(
        DiaryEntry,
        on_delete=models.PROTECT,
        related_name="media_attachments",
        null=True,
        blank=True,
    )
    purpose = models.CharField(max_length=20, choices=Purpose)
    kind = models.CharField(max_length=10, choices=Kind)
    status = models.CharField(
        max_length=12,
        choices=Status,
        default=Status.PENDING,
        db_index=True,
    )
    object_key = models.CharField(max_length=255, unique=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    expected_size = models.PositiveBigIntegerField()
    actual_size = models.PositiveBigIntegerField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalization_token = models.UUIDField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "media_attachment"
        ordering = ("position", "created_at", "pk")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        purpose="score_change",
                        kind="image",
                        expected_size__gte=1,
                        expected_size__lte=10 * 1024 * 1024,
                    )
                    | Q(
                        purpose="comment",
                        kind="image",
                        expected_size__gte=1,
                        expected_size__lte=10 * 1024 * 1024,
                    )
                    | Q(
                        purpose="comment",
                        kind="video",
                        expected_size__gte=1,
                        expected_size__lte=100 * 1024 * 1024,
                    )
                    | Q(
                        purpose="diary_entry",
                        kind="image",
                        expected_size__gte=1,
                        expected_size__lte=10 * 1024 * 1024,
                    )
                    | Q(
                        purpose="diary_entry",
                        kind="video",
                        expected_size__gte=1,
                        expected_size__lte=100 * 1024 * 1024,
                    )
                ),
                name="media_attachment_purpose_kind_size_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        purpose="score_change",
                        comment__isnull=True,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        purpose="comment",
                        score_change__isnull=False,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        purpose="diary_entry",
                        score_change__isnull=True,
                        comment__isnull=True,
                    )
                ),
                name="media_attachment_pending_parent_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="attached",
                        score_change__isnull=False,
                        purpose="score_change",
                        comment__isnull=True,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        status="attached",
                        score_change__isnull=False,
                        purpose="comment",
                        comment__isnull=False,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        status="attached",
                        score_change__isnull=True,
                        purpose="diary_entry",
                        comment__isnull=True,
                        diary_entry__isnull=False,
                    )
                    | Q(
                        status__in=(
                            "pending",
                            "finalizing",
                            "reclaiming",
                            "ready",
                            "deleting",
                        ),
                        purpose="score_change",
                        score_change__isnull=True,
                        comment__isnull=True,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        status__in=(
                            "pending",
                            "finalizing",
                            "reclaiming",
                            "ready",
                            "deleting",
                        ),
                        purpose="comment",
                        score_change__isnull=False,
                        comment__isnull=True,
                        diary_entry__isnull=True,
                    )
                    | Q(
                        status__in=(
                            "pending",
                            "finalizing",
                            "reclaiming",
                            "ready",
                            "deleting",
                        ),
                        purpose="diary_entry",
                        score_change__isnull=True,
                        comment__isnull=True,
                        diary_entry__isnull=True,
                    )
                ),
                name="media_attachment_status_parent_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="pending",
                        actual_size__isnull=True,
                        finalized_at__isnull=True,
                        finalization_token__isnull=True,
                    )
                    | Q(
                        status__in=("finalizing", "reclaiming"),
                        actual_size__isnull=True,
                        finalized_at__isnull=True,
                        finalization_token__isnull=False,
                    )
                    | Q(
                        status__in=("ready", "attached"),
                        actual_size__isnull=False,
                        finalized_at__isnull=False,
                        finalization_token__isnull=True,
                    )
                    | Q(
                        status="deleting",
                        actual_size__isnull=True,
                        finalized_at__isnull=True,
                    )
                    | Q(
                        status="deleting",
                        actual_size__isnull=False,
                        finalized_at__isnull=False,
                        finalization_token__isnull=True,
                    )
                ),
                name="media_attachment_finalization_metadata_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_name} ({self.get_status_display()})"
