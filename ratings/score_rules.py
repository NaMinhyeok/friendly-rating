from dataclasses import dataclass

from django.core.exceptions import ValidationError


@dataclass(frozen=True, slots=True)
class PreparedScoreChange:
    delta: int
    reason: str


def prepare_score_change(
    *,
    delta: int,
    reason: str = "",
) -> PreparedScoreChange:
    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
        raise ValidationError("변경 점수는 0이 아닌 정수여야 합니다.")

    if not isinstance(reason, str):
        raise ValidationError("변경 이유는 문자열이어야 합니다.")

    normalized_reason = reason.strip()
    if len(normalized_reason) > 200:
        raise ValidationError("변경 이유는 200자 이하여야 합니다.")

    return PreparedScoreChange(delta=delta, reason=normalized_reason)


def calculate_resulting_score(
    *,
    current_score: int,
    change: PreparedScoreChange,
) -> int:
    resulting_score = current_score + change.delta
    if not 0 <= resulting_score <= 100:
        raise ValidationError("친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다.")

    return resulting_score
