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

from ..api.serializers import DiaryEntryThreadDataSerializer
from ..models import DiaryEntry, DiaryEntryComment
from ..services import DiaryEntryNotFoundError, create_diary_entry
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db


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
    user = user_model.objects.create_user(username="diary-comment-non-participant")
    client.force_login(user)
    return client, csrf_token


def _post_comment(
    client: Client,
    diary_entry_id: int,
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
            "api-v1:diary-entry-comment-list",
            kwargs={"diary_entry_id": diary_entry_id},
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
    return error


def test_diary_list_summarizes_comments_and_detail_returns_them_in_time_order(
    participant_pair,
):
    entry = create_diary_entry(
        author=participant_pair.first,
        content="댓글을 나눌 기록",
    )
    later_comment = DiaryEntryComment.objects.create(
        diary_entry=entry,
        author=participant_pair.second,
        content="나중 댓글",
    )
    earlier_comment = DiaryEntryComment.objects.create(
        diary_entry=entry,
        author=participant_pair.first,
        content="먼저 댓글",
    )
    now = timezone.now()
    DiaryEntryComment.objects.filter(pk=earlier_comment.pk).update(
        created_at=now - timedelta(minutes=2)
    )
    DiaryEntryComment.objects.filter(pk=later_comment.pk).update(
        created_at=now - timedelta(minutes=1)
    )
    earlier_comment.refresh_from_db()
    later_comment.refresh_from_db()
    client, _ = _participant_client(participant_pair.second)

    list_response = client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )
    detail_response = client.get(
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry.pk},
        ),
        HTTP_ACCEPT="application/json",
    )

    assert list_response.status_code == detail_response.status_code == 200
    summary = list_response.json()["success"]["results"][0]
    assert summary["commentCount"] == 2
    assert summary["threadUrl"] == reverse(
        "diary-entry-thread",
        kwargs={"diary_entry_id": entry.pk},
    )
    assert "comments" not in summary

    detail = detail_response.json()["success"]
    assert detail["commentCount"] == 2
    assert detail["comments"] == [
        {
            "id": earlier_comment.pk,
            "author": {"slot": 1, "displayName": "첫 번째"},
            "content": "먼저 댓글",
            "createdAt": detail["comments"][0]["createdAt"],
            "isMine": False,
        },
        {
            "id": later_comment.pk,
            "author": {"slot": 2, "displayName": "두 번째"},
            "content": "나중 댓글",
            "createdAt": detail["comments"][1]["createdAt"],
            "isMine": True,
        },
    ]
    assert parse_datetime(detail["comments"][0]["createdAt"]) == (
        earlier_comment.created_at
    )
    assert (
        parse_datetime(detail["comments"][1]["createdAt"]) == later_comment.created_at
    )


def test_diary_thread_comment_count_uses_nested_comments_not_stale_annotation(
    participant_pair,
):
    entry = create_diary_entry(author=participant_pair.first, content="함께 볼 기록")
    DiaryEntryComment.objects.bulk_create(
        [
            DiaryEntryComment(
                diary_entry=entry,
                author=participant_pair.first,
                content="첫 댓글",
            ),
            DiaryEntryComment(
                diary_entry=entry,
                author=participant_pair.second,
                content="두 번째 댓글",
            ),
        ]
    )
    stale_entry = (
        DiaryEntry.objects.annotate(comment_count=Value(0, output_field=IntegerField()))
        .prefetch_related("comments")
        .get(pk=entry.pk)
    )

    data = DiaryEntryThreadDataSerializer(
        stale_entry,
        context={"participant_id": participant_pair.first.pk},
    ).data

    assert data["commentCount"] == len(data["comments"]) == 2


def test_comment_author_comes_from_session_and_content_is_normalized(participant_pair):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    client, csrf_token = _participant_client(participant_pair.second)

    response = _post_comment(
        client,
        entry.pk,
        {"content": "  함께 기억하자  "},
        csrf_token=csrf_token,
    )

    comment = DiaryEntryComment.objects.get()
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": comment.pk,
            "author": {"slot": 2, "displayName": "두 번째"},
            "content": "함께 기억하자",
            "createdAt": response.json()["success"]["createdAt"],
            "isMine": True,
        },
    }
    assert comment.diary_entry == entry
    assert comment.author == participant_pair.second
    assert comment.content == "함께 기억하자"


def test_comment_accepts_exactly_five_hundred_characters(participant_pair):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    client, csrf_token = _participant_client(participant_pair.first)
    content = "가" * 500

    response = _post_comment(
        client,
        entry.pk,
        {"content": content},
        csrf_token=csrf_token,
    )

    assert response.status_code == 201
    assert response.json()["success"]["content"] == content
    assert DiaryEntryComment.objects.get().content == content


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        ({}, "content", "REQUIRED"),
        ({"content": ""}, "content", "BLANK"),
        ({"content": "   "}, "content", "BLANK"),
        ({"content": "가" * 501}, "content", "MAX_LENGTH"),
        ({"content": None}, "content", "INVALID_TYPE"),
        ({"content": 7}, "content", "INVALID_TYPE"),
        ({"content": "댓글", "author": 2}, "author", "UNKNOWN_FIELD"),
        (
            {"content": "댓글", "mediaUploadIds": []},
            "mediaUploadIds",
            "UNKNOWN_FIELD",
        ),
    ),
)
def test_comment_strictly_validates_text_only_payload_without_writing(
    participant_pair,
    payload,
    expected_field,
    expected_code,
):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_comment(
        client,
        entry.pk,
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
    assert not DiaryEntryComment.objects.exists()


def test_comment_requires_csrf_without_writing(participant_pair):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = _post_comment(
        client,
        entry.pk,
        {"content": "댓글"},
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    assert not DiaryEntryComment.objects.exists()


@pytest.mark.parametrize("user_kind", ("anonymous", "non_participant"))
def test_only_participants_can_comment(participant_pair, user_kind):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    if user_kind == "anonymous":
        client = Client(enforce_csrf_checks=True)
        csrf_token = None
        expected_error_type = "AUTHENTICATION"
        expected_error_code = "AUTHENTICATION_REQUIRED"
    else:
        client, csrf_token = _non_participant_client()
        expected_error_type = "AUTHORIZATION"
        expected_error_code = "PARTICIPANT_REQUIRED"

    response = _post_comment(
        client,
        entry.pk,
        {"content": "댓글"},
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=403,
        error_type=expected_error_type,
        error_code=expected_error_code,
    )
    assert not DiaryEntryComment.objects.exists()


def test_unknown_diary_entry_rejects_comment_without_writing(participant_pair):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_comment(
        client,
        999_999,
        {"content": "댓글"},
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )
    assert not DiaryEntryComment.objects.exists()


def test_entry_deleted_after_api_read_returns_not_found_without_writing(
    participant_pair,
):
    entry = create_diary_entry(author=participant_pair.first, content="오늘 기록")
    client, csrf_token = _participant_client(participant_pair.second)

    with patch(
        "apps.ratings.api.views.add_diary_entry_comment",
        side_effect=DiaryEntryNotFoundError("일기를 찾을 수 없습니다."),
    ):
        response = _post_comment(
            client,
            entry.pk,
            {"content": "삭제와 겹친 댓글"},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )
    assert not DiaryEntryComment.objects.exists()
