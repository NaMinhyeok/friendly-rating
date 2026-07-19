import json
from collections.abc import Mapping
from typing import Any, cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import resolve, reverse
from django.utils.dateparse import parse_datetime

from ..models import DiaryEntry, ScoreChange
from ..services import create_diary_entry
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db

_NO_BODY = object()


def _participant_client(participant) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    home_response = client.get(reverse("home"))
    assert home_response.status_code == 200
    return client, csrf_token_from_form(home_response, reverse("logout"))


def _login_form_client() -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    login_response = client.get(reverse("login"))
    assert login_response.status_code == 200
    return client, csrf_token_from_form(login_response, None)


def _mutate_json(
    client: Client,
    method: str,
    path: str,
    payload: object = _NO_BODY,
    *,
    csrf_token: str | None,
):
    headers = {
        "HTTP_ACCEPT": "application/json",
        "HTTP_ORIGIN": "http://testserver",
    }
    if csrf_token is not None:
        headers["HTTP_X_CSRFTOKEN"] = csrf_token
    caller = getattr(client, method)
    if payload is _NO_BODY:
        return caller(path, **headers)
    return caller(
        path,
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


def _create_entry(participant, *, content: str = "오늘 기록"):
    return create_diary_entry(
        author=participant,
        content=content,
    )


def _resolve_schema(document, schema):
    resolved = schema
    while isinstance(resolved, Mapping) and "$ref" in resolved:
        reference = resolved["$ref"]
        assert isinstance(reference, str)
        resolved = document
        for part in reference.removeprefix("#/").split("/"):
            resolved = resolved[part]
    assert isinstance(resolved, Mapping)
    return resolved


def test_diary_entry_api_url_names_and_paths_are_stable():
    list_path = reverse("api-v1:diary-entry-list")
    detail_path = reverse(
        "api-v1:diary-entry-detail",
        kwargs={"diary_entry_id": 42},
    )

    assert list_path == "/api/v1/diary-entries/"
    assert detail_path == "/api/v1/diary-entries/42/"
    assert resolve(list_path).url_name == "diary-entry-list"
    assert resolve(detail_path).url_name == "diary-entry-detail"


def test_participant_posts_diary_from_session_with_normalized_content(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _mutate_json(
        client,
        "post",
        reverse("api-v1:diary-entry-list"),
        {"content": "  함께 걸어서 좋았어  "},
        csrf_token=csrf_token,
    )

    entry = DiaryEntry.objects.get()
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": entry.pk,
            "author": {"slot": 1, "displayName": "첫 번째"},
            "content": "함께 걸어서 좋았어",
            "createdAt": response.json()["success"]["createdAt"],
            "updatedAt": None,
            "isMine": True,
        },
    }
    assert entry.author == participant_pair.first
    assert parse_datetime(response.json()["success"]["createdAt"]) == entry.created_at
    assert entry.updated_at is None
    participant_pair.first_to_second.refresh_from_db()
    participant_pair.second_to_first.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert participant_pair.second_to_first.current_score == 0
    assert not ScoreChange.objects.exists()


def test_participant_can_create_exactly_one_thousand_characters(
    participant_pair,
):
    client, csrf_token = _participant_client(participant_pair.second)
    content = "🙂" * 1000

    response = _mutate_json(
        client,
        "post",
        reverse("api-v1:diary-entry-list"),
        {"content": content},
        csrf_token=csrf_token,
    )

    entry = DiaryEntry.objects.get()
    assert response.status_code == 201
    assert entry.author == participant_pair.second
    assert entry.content == content


def test_both_participants_read_the_shared_diary_with_relative_ownership(
    participant_pair,
):
    first_entry = _create_entry(participant_pair.first, content="첫 기록")
    second_entry = _create_entry(participant_pair.second, content="둘째 기록")
    first_client, _ = _participant_client(participant_pair.first)
    second_client, _ = _participant_client(participant_pair.second)

    first_response = first_client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )
    second_response = second_client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )

    assert first_response.status_code == second_response.status_code == 200
    assert first_response.headers["Cache-Control"] == "private, no-store"
    assert second_response.headers["Cache-Control"] == "private, no-store"
    first_results = first_response.json()["success"]["results"]
    second_results = second_response.json()["success"]["results"]
    assert [item["id"] for item in first_results] == [
        second_entry.pk,
        first_entry.pk,
    ]
    assert [item["id"] for item in second_results] == [
        second_entry.pk,
        first_entry.pk,
    ]
    assert [item["isMine"] for item in first_results] == [False, True]
    assert [item["isMine"] for item in second_results] == [True, False]
    assert first_response.json()["success"]["paging"] == {
        "pageNumber": 1,
        "pageSize": 20,
        "hasNext": False,
        "totalCount": 2,
    }


def test_diary_list_uses_twenty_item_pages_in_stable_latest_first_order(
    participant_pair,
):
    for number in range(21):
        _create_entry(participant_pair.first, content=f"기록 {number}")
    client, _ = _participant_client(participant_pair.second)

    first_page = client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )
    second_page = client.get(
        reverse("api-v1:diary-entry-list"),
        {"pageNumber": 2},
        HTTP_ACCEPT="application/json",
    )

    assert [item["content"] for item in first_page.json()["success"]["results"]] == [
        f"기록 {number}" for number in range(20, 0, -1)
    ]
    assert first_page.json()["success"]["paging"] == {
        "pageNumber": 1,
        "pageSize": 20,
        "hasNext": True,
        "totalCount": 21,
    }
    assert [item["content"] for item in second_page.json()["success"]["results"]] == [
        "기록 0"
    ]


@pytest.mark.parametrize("page_number", ("0", "-1", "abc", "1.5"))
def test_diary_list_rejects_invalid_page_numbers(
    participant_pair,
    page_number,
):
    client, _ = _participant_client(participant_pair.first)

    response = client.get(
        reverse("api-v1:diary-entry-list"),
        {"pageNumber": page_number},
        HTTP_ACCEPT="application/json",
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"][0]["field"] == "pageNumber"


def test_diary_list_rejects_unknown_query_fields(participant_pair):
    client, _ = _participant_client(participant_pair.first)

    response = client.get(
        reverse("api-v1:diary-entry-list"),
        {"pageSize": 100},
        HTTP_ACCEPT="application/json",
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"][0]["field"] == "pageSize"


def test_both_participants_can_read_one_shared_diary_entry(participant_pair):
    entry = _create_entry(participant_pair.first)
    client, _ = _participant_client(participant_pair.second)

    response = client.get(
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry.pk},
        ),
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["success"]["id"] == entry.pk
    assert response.json()["success"]["isMine"] is False


def test_author_can_update_only_the_content(participant_pair):
    entry = _create_entry(participant_pair.first, content="수정 전")
    created_at = entry.created_at
    client, csrf_token = _participant_client(participant_pair.first)
    detail_path = reverse(
        "api-v1:diary-entry-detail",
        kwargs={"diary_entry_id": entry.pk},
    )

    response = _mutate_json(
        client,
        "patch",
        detail_path,
        {"content": "  수정한 내용  "},
        csrf_token=csrf_token,
    )

    entry.refresh_from_db()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["success"]["content"] == "수정한 내용"
    assert parse_datetime(response.json()["success"]["createdAt"]) == created_at
    assert parse_datetime(response.json()["success"]["updatedAt"]) == entry.updated_at
    assert entry.content == "수정한 내용"
    assert entry.updated_at is not None


@pytest.mark.parametrize("method", ("patch", "delete"))
def test_non_author_cannot_change_or_delete_a_shared_entry(
    participant_pair,
    method,
):
    entry = _create_entry(participant_pair.first, content="작성자의 기록")
    entry_id = entry.pk
    client, csrf_token = _participant_client(participant_pair.second)
    detail_path = reverse(
        "api-v1:diary-entry-detail",
        kwargs={"diary_entry_id": entry_id},
    )
    payload = {"content": "다른 사람이 바꾼 기록"} if method == "patch" else _NO_BODY

    response = _mutate_json(
        client,
        method,
        detail_path,
        payload,
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PERMISSION_DENIED",
    )
    saved_entry = DiaryEntry.objects.get(pk=entry_id)
    assert saved_entry.content == "작성자의 기록"


def test_author_deletes_an_entry_with_success_null(participant_pair):
    entry = _create_entry(participant_pair.first)
    entry_id = entry.pk
    client, csrf_token = _participant_client(participant_pair.first)

    response = _mutate_json(
        client,
        "delete",
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry_id},
        ),
        csrf_token=csrf_token,
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": None,
    }
    assert not DiaryEntry.objects.filter(pk=entry_id).exists()


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        ({}, "content", "REQUIRED"),
        ({"content": ""}, "content", "BLANK"),
        ({"content": "   "}, "content", "BLANK"),
        ({"content": "가" * 1001}, "content", "MAX_LENGTH"),
        ({"content": None}, "content", "INVALID_TYPE"),
        ({"content": 7}, "content", "INVALID_TYPE"),
        ({"content": "기록", "author": 2}, "author", "UNKNOWN_FIELD"),
        ({"content": "기록", "unexpected": True}, "unexpected", "UNKNOWN_FIELD"),
        (
            {"content": "기록", "entryDate": "2026-07-19"},
            "entryDate",
            "UNKNOWN_FIELD",
        ),
        (None, None, "INVALID_TYPE"),
        ([{"content": "기록"}], None, "INVALID_TYPE"),
    ),
)
def test_create_diary_entry_strictly_validates_input_without_writing(
    participant_pair,
    payload,
    expected_field,
    expected_code,
):
    client, csrf_token = _participant_client(participant_pair.first)

    response = _mutate_json(
        client,
        "post",
        reverse("api-v1:diary-entry-list"),
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
    assert not DiaryEntry.objects.exists()


def test_empty_patch_is_rejected_without_touching_the_entry(participant_pair):
    entry = _create_entry(participant_pair.first)
    client, csrf_token = _participant_client(participant_pair.first)

    response = _mutate_json(
        client,
        "patch",
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry.pk},
        ),
        {},
        csrf_token=csrf_token,
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert any(
        detail["field"] == "content" and detail["code"] == "REQUIRED"
        for detail in error["details"]
    )


def test_patch_rejects_the_removed_entry_date_field(participant_pair):
    entry = _create_entry(participant_pair.first)
    client, csrf_token = _participant_client(participant_pair.first)

    response = _mutate_json(
        client,
        "patch",
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry.pk},
        ),
        {"content": "바꾸려던 기록", "entryDate": "2026-07-19"},
        csrf_token=csrf_token,
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert any(
        detail["field"] == "entryDate" and detail["code"] == "UNKNOWN_FIELD"
        for detail in error["details"]
    )
    entry.refresh_from_db()
    assert entry.content == "오늘 기록"


@pytest.mark.parametrize("method", ("post", "patch", "delete"))
def test_diary_mutations_require_csrf_without_writing(participant_pair, method):
    entry = _create_entry(participant_pair.first)
    entry_id = entry.pk
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)
    path = (
        reverse("api-v1:diary-entry-list")
        if method == "post"
        else reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": entry_id},
        )
    )
    payload = {"content": "새 기록"} if method != "delete" else _NO_BODY

    response = _mutate_json(
        client,
        method,
        path,
        payload,
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    assert DiaryEntry.objects.filter(pk=entry_id, content="오늘 기록").exists()
    assert DiaryEntry.objects.count() == 1


def test_anonymous_and_non_participant_cannot_read_shared_diary(participant_pair):
    _create_entry(participant_pair.first)
    anonymous_client = Client(enforce_csrf_checks=True)
    anonymous_response = anonymous_client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        anonymous_response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )

    user_model = cast(type[User], get_user_model())
    non_participant = user_model.objects.create_user(username="diary-outsider")
    anonymous_client.force_login(non_participant)
    forbidden_response = anonymous_client.get(
        reverse("api-v1:diary-entry-list"),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        forbidden_response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )


def test_authenticated_non_participant_cannot_create_a_diary_entry(participant_pair):
    client, csrf_token = _login_form_client()
    user_model = cast(type[User], get_user_model())
    non_participant = user_model.objects.create_user(username="diary-writer-outsider")
    client.force_login(non_participant)

    response = _mutate_json(
        client,
        "post",
        reverse("api-v1:diary-entry-list"),
        {"content": "보이면 안 되는 기록"},
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )
    assert not DiaryEntry.objects.exists()


def test_missing_diary_entry_returns_not_found(participant_pair):
    client, _ = _participant_client(participant_pair.first)

    response = client.get(
        reverse(
            "api-v1:diary-entry-detail",
            kwargs={"diary_entry_id": 999999},
        ),
        HTTP_ACCEPT="application/json",
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )


def test_diary_openapi_declares_shared_read_and_author_mutation_contract(client):
    document = client.get(
        reverse("api-schema"),
        HTTP_ACCEPT="application/json",
    ).json()
    list_operations = document["paths"]["/api/v1/diary-entries/"]
    detail_operations = document["paths"]["/api/v1/diary-entries/{diary_entry_id}/"]

    assert set(list_operations) == {"get", "post"}
    assert set(detail_operations) == {"get", "patch", "delete"}
    assert list_operations["get"]["security"] == [{"cookieAuth": []}]
    assert set(list_operations["get"]["responses"]) == {
        "200",
        "400",
        "403",
        "404",
        "406",
        "500",
    }

    create = list_operations["post"]
    create_csrf = next(
        parameter
        for parameter in create["parameters"]
        if parameter["name"] == "X-CSRFToken"
    )
    assert create_csrf["required"] is True
    create_schema = _resolve_schema(
        document,
        create["requestBody"]["content"]["application/json"]["schema"],
    )
    assert create["requestBody"]["required"] is True
    assert create_schema["additionalProperties"] is False
    assert create_schema["required"] == ["content"]
    assert set(create_schema["properties"]) == {"content"}
    assert create_schema["properties"]["content"]["maxLength"] == 1000

    patch = detail_operations["patch"]
    patch_schema = _resolve_schema(
        document,
        patch["requestBody"]["content"]["application/json"]["schema"],
    )
    assert patch["requestBody"]["required"] is True
    assert patch_schema["additionalProperties"] is False
    assert patch_schema["required"] == ["content"]
    assert set(patch_schema["properties"]) == {"content"}
    assert patch["responses"]["403"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MutationForbiddenErrorEnvelope"
    }

    delete = detail_operations["delete"]
    assert "requestBody" not in delete
    deleted_envelope = _resolve_schema(
        document,
        delete["responses"]["200"]["content"]["application/json"]["schema"],
    )
    assert set(deleted_envelope["required"]) == {"resultType", "error", "success"}
    assert (
        _resolve_schema(document, deleted_envelope["properties"]["success"])["type"]
        == "null"
    )

    entry_schema = document["components"]["schemas"]["DiaryEntryData"]
    assert set(entry_schema["properties"]) == {
        "id",
        "author",
        "content",
        "createdAt",
        "updatedAt",
        "isMine",
    }
    assert entry_schema["properties"]["content"]["maxLength"] == 1000
    assert set(entry_schema["properties"]["updatedAt"]["type"]) == {
        "string",
        "null",
    }
    assert entry_schema["properties"]["updatedAt"]["format"] == "date-time"
