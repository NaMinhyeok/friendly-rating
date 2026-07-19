import json
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db.models import IntegerField, Value
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..api.serializers import ScoreChangeThreadDataSerializer
from ..models import ScoreChange, ScoreChangeComment
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db


def _create_change(participant_pair) -> ScoreChange:
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=3,
        reason="고마워",
        resulting_score=13,
    )


def _participant_client(participant) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    home_response = client.get(reverse("home"))
    assert home_response.status_code == 200
    return client, csrf_token_from_form(home_response, reverse("logout"))


def _non_participant_client() -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    login_response = client.get(reverse("login"))
    assert login_response.status_code == 200
    csrf_token = csrf_token_from_form(login_response, None)
    user_model = cast(type[User], get_user_model())
    user = user_model.objects.create_user(username="thread-non-participant")
    client.force_login(user)
    return client, csrf_token


def _post_comment(
    client: Client,
    score_change: ScoreChange,
    payload: object,
    *,
    csrf_token: str | None,
):
    headers = {
        "HTTP_ACCEPT": "application/json",
        "HTTP_ORIGIN": "http://testserver",
    }
    if csrf_token is not None:
        headers["HTTP_X_CSRFTOKEN"] = csrf_token
    return client.post(
        reverse(
            "api-v1:score-change-comment-list",
            kwargs={"score_change_id": score_change.pk},
        ),
        data=json.dumps(payload, ensure_ascii=False),
        content_type="application/json",
        **headers,
    )


def _assert_error(
    response,
    *,
    status_code: int,
    error_type: str,
    error_code: str,
) -> dict[str, Any]:
    assert response.status_code == status_code
    assert response.headers["Content-Type"] == "application/json"
    body = response.json()
    assert set(body) == {"resultType", "error", "success"}
    assert body["resultType"] == "ERROR"
    assert body["success"] is None
    error = body["error"]
    assert isinstance(error, dict)
    assert set(error) == {"errorType", "errorCode", "reason", "details"}
    assert error["errorType"] == error_type
    assert error["errorCode"] == error_code
    assert isinstance(error["reason"], str)
    assert error["reason"]
    assert isinstance(error["details"], list)
    return error


def test_participant_reads_score_change_thread_in_comment_time_order(
    participant_pair,
):
    change = _create_change(participant_pair)
    later_comment = ScoreChangeComment.objects.create(
        score_change=change,
        author=participant_pair.second,
        content="나중 댓글",
    )
    earlier_comment = ScoreChangeComment.objects.create(
        score_change=change,
        author=participant_pair.first,
        content="먼저 댓글",
    )
    now = timezone.now()
    ScoreChangeComment.objects.filter(pk=earlier_comment.pk).update(
        created_at=now - timedelta(minutes=2)
    )
    ScoreChangeComment.objects.filter(pk=later_comment.pk).update(
        created_at=now - timedelta(minutes=1)
    )
    earlier_comment.refresh_from_db()
    later_comment.refresh_from_db()
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse(
            "api-v1:score-change-detail",
            kwargs={"score_change_id": change.pk},
        ),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == "private, no-store"
    body = response.json()
    assert body == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": change.pk,
            "sourceParticipant": {
                "slot": 1,
                "displayName": "첫 번째",
            },
            "targetParticipant": {
                "slot": 2,
                "displayName": "두 번째",
            },
            "changedBy": {
                "slot": 1,
                "displayName": "첫 번째",
            },
            "delta": 3,
            "reason": "고마워",
            "resultingScore": 13,
            "createdAt": body["success"]["createdAt"],
            "commentCount": 2,
            "threadUrl": reverse(
                "score-change-thread",
                kwargs={"score_change_id": change.pk},
            ),
            "comments": [
                {
                    "id": earlier_comment.pk,
                    "author": {
                        "slot": 1,
                        "displayName": "첫 번째",
                    },
                    "content": "먼저 댓글",
                    "createdAt": body["success"]["comments"][0]["createdAt"],
                    "isMine": True,
                },
                {
                    "id": later_comment.pk,
                    "author": {
                        "slot": 2,
                        "displayName": "두 번째",
                    },
                    "content": "나중 댓글",
                    "createdAt": body["success"]["comments"][1]["createdAt"],
                    "isMine": False,
                },
            ],
        },
    }
    assert parse_datetime(body["success"]["createdAt"]) == change.created_at
    assert parse_datetime(body["success"]["comments"][0]["createdAt"]) == (
        earlier_comment.created_at
    )
    assert parse_datetime(body["success"]["comments"][1]["createdAt"]) == (
        later_comment.created_at
    )


def test_thread_comment_count_uses_prefetched_comments_not_stale_annotation(
    participant_pair,
):
    change = _create_change(participant_pair)
    ScoreChangeComment.objects.bulk_create(
        [
            ScoreChangeComment(
                score_change=change,
                author=participant_pair.first,
                content="첫 댓글",
            ),
            ScoreChangeComment(
                score_change=change,
                author=participant_pair.second,
                content="두 번째 댓글",
            ),
        ]
    )
    stale_change = (
        ScoreChange.objects.annotate(
            comment_count=Value(0, output_field=IntegerField()),
        )
        .prefetch_related("comments")
        .get(pk=change.pk)
    )

    data = ScoreChangeThreadDataSerializer(
        stale_change,
        context={"participant_id": participant_pair.first.pk},
    ).data

    assert data["commentCount"] == len(data["comments"]) == 2


def test_comment_author_comes_from_session_and_notification_waits_for_commit(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    change = _create_change(participant_pair)
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.score_change_comments.send_score_comment_notification"
    ) as send_notification:
        with django_capture_on_commit_callbacks(execute=True):
            response = _post_comment(
                client,
                change,
                {"content": "  이야기해 보자  "},
                csrf_token=csrf_token,
            )
            send_notification.assert_not_called()

        send_notification.assert_called_once_with(
            recipient_id=participant_pair.second.pk,
            score_change_id=change.pk,
        )

    comment = ScoreChangeComment.objects.get()
    assert response.status_code == 201
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": comment.pk,
            "author": {
                "slot": 1,
                "displayName": "첫 번째",
            },
            "content": "이야기해 보자",
            "createdAt": response.json()["success"]["createdAt"],
            "isMine": True,
        },
    }
    assert parse_datetime(response.json()["success"]["createdAt"]) == (
        comment.created_at
    )
    assert comment.score_change == change
    assert comment.author == participant_pair.first
    assert comment.content == "이야기해 보자"


def test_comment_accepts_exactly_five_hundred_characters(participant_pair):
    change = _create_change(participant_pair)
    client, csrf_token = _participant_client(participant_pair.second)
    content = "가" * 500

    response = _post_comment(
        client,
        change,
        {"content": content},
        csrf_token=csrf_token,
    )

    comment = ScoreChangeComment.objects.get()
    assert response.status_code == 201
    assert response.json()["success"]["content"] == content
    assert response.json()["success"]["isMine"] is True
    assert comment.author == participant_pair.second
    assert comment.content == content


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        ({}, "content", "REQUIRED"),
        ({"content": ""}, "content", "BLANK"),
        ({"content": "   "}, "content", "BLANK"),
        ({"content": "가" * 501}, "content", "MAX_LENGTH"),
        ({"content": None}, "content", "INVALID_TYPE"),
        ({"content": 7}, "content", "INVALID_TYPE"),
        (
            {"content": "본문", "author": 2},
            "author",
            "UNKNOWN_FIELD",
        ),
    ),
)
def test_comment_strictly_validates_content_without_writing(
    participant_pair,
    payload,
    expected_field,
    expected_code,
):
    change = _create_change(participant_pair)
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_comment(
        client,
        change,
        payload,
        csrf_token=csrf_token,
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert any(
        detail["field"] == expected_field and detail["code"] == expected_code
        for detail in error["details"]
    )
    assert not ScoreChangeComment.objects.exists()


def test_comment_requires_csrf_without_writing(participant_pair):
    change = _create_change(participant_pair)
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = _post_comment(
        client,
        change,
        {"content": "댓글"},
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    assert not ScoreChangeComment.objects.exists()


@pytest.mark.parametrize("request_kind", ("detail", "comment"))
def test_anonymous_user_cannot_read_or_comment(
    participant_pair,
    request_kind,
):
    change = _create_change(participant_pair)
    client = Client(enforce_csrf_checks=True)

    if request_kind == "detail":
        response = client.get(
            reverse(
                "api-v1:score-change-detail",
                kwargs={"score_change_id": change.pk},
            ),
            HTTP_ACCEPT="application/json",
        )
    else:
        response = _post_comment(
            client,
            change,
            {"content": "댓글"},
            csrf_token=None,
        )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )
    assert "Location" not in response.headers
    assert not ScoreChangeComment.objects.exists()


@pytest.mark.parametrize("request_kind", ("detail", "comment"))
def test_authenticated_non_participant_cannot_read_or_comment(
    participant_pair,
    request_kind,
):
    change = _create_change(participant_pair)
    client, csrf_token = _non_participant_client()

    if request_kind == "detail":
        response = client.get(
            reverse(
                "api-v1:score-change-detail",
                kwargs={"score_change_id": change.pk},
            ),
            HTTP_ACCEPT="application/json",
        )
    else:
        response = _post_comment(
            client,
            change,
            {"content": "댓글"},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )
    assert not ScoreChangeComment.objects.exists()


def test_unknown_score_change_returns_not_found_for_detail(participant_pair):
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = client.get(
        reverse(
            "api-v1:score-change-detail",
            kwargs={"score_change_id": 999_999},
        ),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )


def test_unknown_score_change_rejects_comment_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)

    response = client.post(
        reverse(
            "api-v1:score-change-comment-list",
            kwargs={"score_change_id": 999_999},
        ),
        data=json.dumps({"content": "댓글"}, ensure_ascii=False),
        content_type="application/json",
        HTTP_ACCEPT="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )
    assert not ScoreChangeComment.objects.exists()


def test_notification_failure_does_not_undo_comment_creation(
    participant_pair,
    django_capture_on_commit_callbacks,
):
    change = _create_change(participant_pair)
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.score_change_comments.send_score_comment_notification",
        side_effect=RuntimeError("FCM unavailable"),
    ) as send_notification:
        with django_capture_on_commit_callbacks(execute=True):
            response = _post_comment(
                client,
                change,
                {"content": "알림과 별개로 저장해 줘"},
                csrf_token=csrf_token,
            )

    assert response.status_code == 201
    assert ScoreChangeComment.objects.filter(
        score_change=change,
        author=participant_pair.first,
        content="알림과 별개로 저장해 줘",
    ).exists()
    send_notification.assert_called_once_with(
        recipient_id=participant_pair.second.pk,
        score_change_id=change.pk,
    )
