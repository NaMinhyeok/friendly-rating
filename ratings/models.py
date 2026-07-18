from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Participant(models.Model):
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
        ordering = ("slot",)
        constraints = [
            models.CheckConstraint(
                condition=Q(slot__in=(1, 2)),
                name="participant_slot_is_first_or_second",
            ),
        ]

    def __str__(self):
        return self.display_name


class RelationshipScore(models.Model):
    rater = models.OneToOneField(
        Participant,
        on_delete=models.PROTECT,
        related_name="outgoing_score",
    )
    recipient = models.OneToOneField(
        Participant,
        on_delete=models.PROTECT,
        related_name="incoming_score",
    )
    value = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(value__gte=0) & Q(value__lte=100),
                name="relationship_score_between_0_and_100",
            ),
            models.CheckConstraint(
                condition=~Q(rater=F("recipient")),
                name="relationship_score_between_different_participants",
            ),
        ]

    def __str__(self):
        return f"{self.rater} → {self.recipient}: {self.value}"


class ImmutableScoreChangeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("점수 변경 기록은 수정할 수 없습니다.")

    def delete(self):
        raise ValidationError("점수 변경 기록은 삭제할 수 없습니다.")


class ScoreChange(models.Model):
    score = models.ForeignKey(
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
    reason = models.CharField(max_length=200)
    resulting_score = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ImmutableScoreChangeQuerySet.as_manager()

    class Meta:
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

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("점수 변경 기록은 수정할 수 없습니다.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("점수 변경 기록은 삭제할 수 없습니다.")

    def __str__(self):
        sign = "+" if self.delta > 0 else ""
        return f"{self.changed_by}: {sign}{self.delta} → {self.resulting_score}"
