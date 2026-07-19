from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection

from ..models import ScoreChange
from ..services import (
    ScoreUnchangedError,
    change_relationship_score,
    set_relationship_score,
)

pytestmark = pytest.mark.django_db


def test_change_persists_history_and_only_updates_the_source_direction(
    participant_pair,
):
    participant_pair.first_to_second.current_score = 10
    participant_pair.first_to_second.save(update_fields=("current_score",))

    returned_change = change_relationship_score(
        source_participant=participant_pair.first,
        delta=7,
        reason="  오늘 많이 도와줘서  ",
    )

    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    persisted_change = ScoreChange.objects.get()

    assert participant_pair.first_to_second.current_score == 17
    assert participant_pair.second_to_first.current_score == 0
    assert returned_change == persisted_change
    assert persisted_change.relationship_score == participant_pair.first_to_second
    assert persisted_change.changed_by == participant_pair.first
    assert persisted_change.delta == 7
    assert persisted_change.reason == "오늘 많이 도와줘서"
    assert persisted_change.resulting_score == 17


def test_rejected_change_leaves_score_and_history_unchanged(participant_pair):
    with pytest.raises(ValidationError) as raised:
        change_relationship_score(
            source_participant=participant_pair.first,
            delta=-1,
            reason="범위를 벗어나요",
        )

    participant_pair.first_to_second.refresh_from_db()
    assert raised.value.messages == [
        "친밀도는 0점보다 낮거나 100점보다 높을 수 없습니다."
    ]
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()


def test_history_write_failure_rolls_back_the_score_update(participant_pair):
    def fail_history_insert(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith('INSERT INTO "SCORE_CHANGE"'):
            raise OperationalError("history write failed")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_history_insert):
        with pytest.raises(OperationalError, match="history write failed"):
            change_relationship_score(
                source_participant=participant_pair.first,
                delta=5,
                reason="원자성 검증",
            )

    participant_pair.first_to_second.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()


def test_set_score_persists_the_effective_delta_and_only_updates_source_direction(
    participant_pair,
):
    participant_pair.first_to_second.current_score = 10
    participant_pair.first_to_second.save(update_fields=("current_score",))
    participant_pair.second_to_first.current_score = 30
    participant_pair.second_to_first.save(update_fields=("current_score",))

    returned_change = set_relationship_score(
        source_participant=participant_pair.first,
        target_score=100,
        reason="  최종 점수로 기록  ",
    )

    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    persisted_change = ScoreChange.objects.get()

    assert participant_pair.first_to_second.current_score == 100
    assert participant_pair.second_to_first.current_score == 30
    assert returned_change == persisted_change
    assert persisted_change.relationship_score == participant_pair.first_to_second
    assert persisted_change.changed_by == participant_pair.first
    assert persisted_change.delta == 90
    assert persisted_change.reason == "최종 점수로 기록"
    assert persisted_change.resulting_score == 100


def test_set_score_rejects_the_current_score_without_writing_or_notifying(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    participant_pair.first_to_second.current_score = 25
    participant_pair.first_to_second.save(update_fields=("current_score",))

    with patch(
        "apps.ratings.services.score_changes.send_score_change_notification"
    ) as send_push:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(ScoreUnchangedError):
                set_relationship_score(
                    source_participant=participant_pair.first,
                    target_score=25,
                    reason="변경 없음",
                )

    participant_pair.first_to_second.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 25
    assert not ScoreChange.objects.exists()
    assert callbacks == []
    send_push.assert_not_called()
