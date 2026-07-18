from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Participant, RelationshipScore, ScoreChange


@transaction.atomic
def change_relationship_score(
    *,
    rater: Participant,
    delta: int,
    reason: str,
) -> ScoreChange:
    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
        raise ValidationError("변경 점수는 0이 아닌 정수여야 합니다.")

    if not isinstance(reason, str):
        raise ValidationError("변경 이유를 입력해 주세요.")

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValidationError("변경 이유를 입력해 주세요.")
    if len(normalized_reason) > 200:
        raise ValidationError("변경 이유는 200자 이하여야 합니다.")

    score = RelationshipScore.objects.select_for_update().get(rater=rater)
    resulting_score = score.value + delta
    if not 0 <= resulting_score <= 100:
        raise ValidationError("친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다.")

    score.value = resulting_score
    score.save(update_fields=("value", "updated_at"))

    return ScoreChange.objects.create(
        score=score,
        changed_by=rater,
        delta=delta,
        reason=normalized_reason,
        resulting_score=resulting_score,
    )
