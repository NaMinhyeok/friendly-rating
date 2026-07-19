import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import Client
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..media_storage import MediaStorageGateway, StoredMediaObject
from ..models import MediaAttachment, ScoreChange, ScoreChangeComment
from .http_helpers import csrf_token_from_form

pytestmark = pytest.mark.django_db


@dataclass
class FakeMediaStorage(MediaStorageGateway):
    stored_object: StoredMediaObject | None = None
    upload_requests: list[tuple[str, str, int, int]] = field(default_factory=list)
    inspect_requests: list[str] = field(default_factory=list)
    promotion_requests: list[tuple[str, str, str, str]] = field(default_factory=list)
    deletion_requests: list[str] = field(default_factory=list)
    deletion_failures: set[str] = field(default_factory=set)

    def generate_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        self.upload_requests.append(
            (object_key, content_type, content_length, expires_in)
        )
        return f"https://uploads.example.test/{object_key}"

    def inspect_object(self, *, object_key: str) -> StoredMediaObject:
        self.inspect_requests.append(object_key)
        if self.stored_object is None:
            raise AssertionError("The test did not configure an uploaded object.")
        return self.stored_object

    def promote_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
        original_name: str,
    ) -> None:
        self.promotion_requests.append(
            (source_key, destination_key, content_type, original_name)
        )

    def delete_object(self, *, object_key: str) -> None:
        self.deletion_requests.append(object_key)
        if object_key in self.deletion_failures:
            raise RuntimeError("simulated R2 delete failure")

    def generate_download_url(
        self,
        *,
        object_key: str,
        content_type: str,
        original_name: str,
        expires_in: int,
    ) -> str:
        raise AssertionError("Download signing is outside this API workflow.")


def _create_change(participant_pair) -> ScoreChange:
    return ScoreChange.objects.create(
        relationship_score=participant_pair.first_to_second,
        changed_by=participant_pair.first,
        delta=1,
        reason="첨부할 대화",
        resulting_score=1,
    )


def _participant_client(participant) -> tuple[Client, str]:
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    response = client.get(reverse("home"))
    assert response.status_code == 200
    return client, csrf_token_from_form(response, reverse("logout"))


def _post_json(
    client: Client,
    url: str,
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
        url,
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
    assert isinstance(error["reason"], str) and error["reason"]
    assert isinstance(error["details"], list)
    return error


def _score_image_payload() -> dict[str, object]:
    return {
        "purpose": "scoreChange",
        "kind": "image",
        "fileName": "photo.jpg",
        "contentType": "image/jpeg",
        "byteSize": 512,
    }


def test_media_upload_api_url_names_and_paths_are_stable():
    initiate_path = reverse("api-v1:media-upload-list")
    upload_id = "00000000-0000-4000-8000-000000000001"
    complete_path = reverse(
        "api-v1:media-upload-complete",
        kwargs={"upload_id": upload_id},
    )
    discard_path = reverse(
        "api-v1:media-upload-discard",
        kwargs={"upload_id": upload_id},
    )

    assert initiate_path == "/api/v1/media-uploads/"
    assert resolve(initiate_path).url_name == "media-upload-list"
    assert complete_path == f"/api/v1/media-uploads/{upload_id}/complete/"
    assert resolve(complete_path).url_name == "media-upload-complete"
    assert discard_path == f"/api/v1/media-uploads/{upload_id}/discard/"
    assert resolve(discard_path).url_name == "media-upload-discard"


def test_participant_initiates_and_completes_a_private_direct_upload(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    settings.MEDIA_UPLOAD_URL_TTL_SECONDS = 120
    storage = FakeMediaStorage()
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        initiate_response = _post_json(
            client,
            reverse("api-v1:media-upload-list"),
            _score_image_payload(),
            csrf_token=csrf_token,
        )

        assert initiate_response.status_code == 201
        assert initiate_response.headers["Cache-Control"] == "private, no-store"
        initiated = initiate_response.json()["success"]
        upload_id = initiated["uploadId"]
        attachment = MediaAttachment.objects.get(pk=upload_id)
        assert initiated == {
            "uploadId": str(attachment.pk),
            "uploadUrl": f"https://uploads.example.test/{attachment.object_key}",
            "requiredHeaders": {
                "Content-Type": "image/jpeg",
                "Cache-Control": "private, no-store, max-age=0",
            },
            "expiresAt": initiated["expiresAt"],
        }
        assert parse_datetime(initiated["expiresAt"]) == attachment.expires_at
        assert attachment.status == MediaAttachment.Status.PENDING
        assert attachment.object_key == f"pending/{attachment.pk}"
        assert storage.upload_requests == [
            (attachment.object_key, "image/jpeg", 512, 120)
        ]

        storage.stored_object = StoredMediaObject(
            size=512,
            content_type="image/jpeg",
            etag="test-etag",
            initial_bytes=b"\xff\xd8\xffimage-data",
        )
        complete_response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-complete",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    attachment.refresh_from_db()
    assert complete_response.status_code == 200
    assert complete_response.headers["Cache-Control"] == "private, no-store"
    assert complete_response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": {
            "id": str(attachment.pk),
            "kind": "image",
            "fileName": "photo.jpg",
            "contentType": "image/jpeg",
            "byteSize": 512,
        },
    }
    assert attachment.status == MediaAttachment.Status.READY
    prefix, key_upload_id, claim_token = attachment.object_key.split("/")
    assert prefix == "media"
    assert key_upload_id == str(attachment.pk)
    assert UUID(claim_token).version == 4
    assert attachment.finalization_token is None
    assert storage.inspect_requests == [
        f"pending/{attachment.pk}",
        attachment.object_key,
    ]
    assert storage.promotion_requests == [
        (
            f"pending/{attachment.pk}",
            attachment.object_key,
            "image/jpeg",
            "photo.jpg",
        )
    ]
    assert storage.deletion_requests == [f"pending/{attachment.pk}"]


def test_uploader_discards_an_unattached_private_upload(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    upload_id = uuid4()
    attachment = MediaAttachment.objects.create(
        id=upload_id,
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key=f"pending/{upload_id}",
        original_name="discard.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    storage = FakeMediaStorage()
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-discard",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        side_effect=AssertionError("A missing retry must not configure storage."),
    ):
        repeated_response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-discard",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "resultType": "SUCCESS",
        "error": None,
        "success": None,
    }
    assert not MediaAttachment.objects.filter(pk=attachment.pk).exists()
    assert storage.deletion_requests == [f"pending/{attachment.pk}"]
    assert repeated_response.status_code == 200
    assert repeated_response.json() == response.json()


def test_only_the_uploader_can_discard_an_upload(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key="pending/discard-owner-api",
        original_name="owner.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    storage = FakeMediaStorage()
    client, csrf_token = _participant_client(participant_pair.second)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-discard",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PERMISSION_DENIED",
    )
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.PENDING
    assert storage.deletion_requests == []


def test_discard_refuses_an_already_attached_upload(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    change = _create_change(participant_pair)
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        score_change=change,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.ATTACHED,
        object_key="media/discard-attached-api",
        original_name="attached.jpg",
        content_type="image/jpeg",
        expected_size=512,
        actual_size=512,
        etag="attached-etag",
        expires_at=timezone.now() + timedelta(minutes=10),
        finalized_at=timezone.now(),
    )
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        side_effect=AssertionError("Attached discard must not access storage."),
    ):
        response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-discard",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=409,
        error_type="CONFLICT",
        error_code="MEDIA_UPLOAD_CONFLICT",
    )
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.ATTACHED
    assert attachment.score_change == change


def test_discard_requires_csrf_without_changing_the_upload(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key="pending/discard-csrf-api",
        original_name="csrf.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = _post_json(
        client,
        reverse(
            "api-v1:media-upload-discard",
            kwargs={"upload_id": attachment.pk},
        ),
        {},
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.PENDING


def test_discard_storage_failure_returns_503_and_keeps_a_retryable_tombstone(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    upload_id = uuid4()
    pending_key = f"pending/{upload_id}"
    attachment = MediaAttachment.objects.create(
        id=upload_id,
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key=pending_key,
        original_name="failed-discard.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    storage = FakeMediaStorage(deletion_failures={pending_key})
    client, csrf_token = _participant_client(participant_pair.first)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-discard",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=503,
        error_type="SERVER",
        error_code="MEDIA_UPLOADS_UNAVAILABLE",
    )
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.DELETING
    assert attachment.expires_at <= timezone.now()
    assert storage.deletion_requests == [pending_key]


def test_discard_rejects_nonempty_json_before_changing_state(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key="pending/strict-discard",
        original_name="strict-discard.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        reverse(
            "api-v1:media-upload-discard",
            kwargs={"upload_id": attachment.pk},
        ),
        {"force": True},
        csrf_token=csrf_token,
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"] == [
        {
            "field": "force",
            "code": "UNKNOWN_FIELD",
            "message": "알 수 없는 필드입니다.",
        }
    ]
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.PENDING


def test_anonymous_media_initiation_requires_authentication(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client = Client(enforce_csrf_checks=True)

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
        _score_image_payload(),
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="AUTHENTICATION_REQUIRED",
    )
    assert not MediaAttachment.objects.exists()


def test_authenticated_media_initiation_requires_csrf(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant_pair.first.user)

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
        _score_image_payload(),
        csrf_token=None,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHENTICATION",
        error_code="CSRF_FAILED",
    )
    assert not MediaAttachment.objects.exists()


def test_authenticated_non_participant_cannot_initiate_media(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client = Client(enforce_csrf_checks=True)
    login_response = client.get(reverse("login"))
    csrf_token = csrf_token_from_form(login_response, None)
    user_model = cast(type[User], get_user_model())
    client.force_login(user_model.objects.create_user(username="media-outsider"))

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
        _score_image_payload(),
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PARTICIPANT_REQUIRED",
    )
    assert not MediaAttachment.objects.exists()


@pytest.mark.parametrize(
    ("payload", "expected_field", "expected_code"),
    (
        (
            {**_score_image_payload(), "unexpected": True},
            "unexpected",
            "UNKNOWN_FIELD",
        ),
        (
            {**_score_image_payload(), "kind": "video", "contentType": "video/mp4"},
            "kind",
            "UNSUPPORTED_KIND",
        ),
        (
            {
                **_score_image_payload(),
                "purpose": "comment",
            },
            "scoreChangeId",
            "REQUIRED",
        ),
        (
            {**_score_image_payload(), "scoreChangeId": 1},
            "scoreChangeId",
            "FORBIDDEN",
        ),
        (
            {**_score_image_payload(), "byteSize": True},
            "byteSize",
            "INVALID_TYPE",
        ),
    ),
)
def test_media_initiation_strictly_validates_the_intent_before_writing(
    participant_pair,
    settings,
    payload,
    expected_field,
    expected_code,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
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
    assert not MediaAttachment.objects.exists()


def test_comment_upload_requires_an_existing_accessible_score_change(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client, csrf_token = _participant_client(participant_pair.first)
    payload = {
        "purpose": "comment",
        "kind": "image",
        "fileName": "reply.png",
        "contentType": "image/png",
        "byteSize": 256,
        "scoreChangeId": 999_999,
    }

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
        payload,
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=404,
        error_type="NOT_FOUND",
        error_code="NOT_FOUND",
    )
    assert not MediaAttachment.objects.exists()


def test_only_the_uploader_can_complete_a_pending_upload(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    storage = FakeMediaStorage()
    first_client, first_csrf = _participant_client(participant_pair.first)
    second_client, second_csrf = _participant_client(participant_pair.second)

    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        initiated = _post_json(
            first_client,
            reverse("api-v1:media-upload-list"),
            _score_image_payload(),
            csrf_token=first_csrf,
        ).json()["success"]
        response = _post_json(
            second_client,
            reverse(
                "api-v1:media-upload-complete",
                kwargs={"upload_id": initiated["uploadId"]},
            ),
            {},
            csrf_token=second_csrf,
        )

    _assert_error(
        response,
        status_code=403,
        error_type="AUTHORIZATION",
        error_code="PERMISSION_DENIED",
    )
    attachment = MediaAttachment.objects.get(pk=initiated["uploadId"])
    assert attachment.status == MediaAttachment.Status.PENDING
    assert storage.inspect_requests == []


def test_complete_endpoint_rejects_nonempty_json_before_changing_state(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.PENDING,
        object_key="pending/strict-complete",
        original_name="strict.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        reverse(
            "api-v1:media-upload-complete",
            kwargs={"upload_id": attachment.pk},
        ),
        {"etag": "client-controlled"},
        csrf_token=csrf_token,
    )

    error = _assert_error(
        response,
        status_code=400,
        error_type="VALIDATION",
        error_code="INVALID_INPUT",
    )
    assert error["details"] == [
        {
            "field": "etag",
            "code": "UNKNOWN_FIELD",
            "message": "알 수 없는 필드입니다.",
        }
    ]
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.PENDING


def test_completion_reports_a_stable_conflict_for_an_upload_in_progress(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    active_claim_token = uuid4()
    attachment = MediaAttachment.objects.create(
        uploader=participant_pair.first,
        purpose=MediaAttachment.Purpose.SCORE_CHANGE,
        kind=MediaAttachment.Kind.IMAGE,
        status=MediaAttachment.Status.FINALIZING,
        object_key="pending/already-finalizing",
        original_name="processing.jpg",
        content_type="image/jpeg",
        expected_size=512,
        expires_at=timezone.now() + timedelta(minutes=10),
        finalization_token=active_claim_token,
    )
    client, csrf_token = _participant_client(participant_pair.first)

    storage = FakeMediaStorage()
    with patch(
        "apps.ratings.services.media_uploads.get_media_storage_gateway",
        return_value=storage,
    ):
        response = _post_json(
            client,
            reverse(
                "api-v1:media-upload-complete",
                kwargs={"upload_id": attachment.pk},
            ),
            {},
            csrf_token=csrf_token,
        )

    _assert_error(
        response,
        status_code=409,
        error_type="CONFLICT",
        error_code="MEDIA_UPLOAD_CONFLICT",
    )
    attachment.refresh_from_db()
    assert attachment.status == MediaAttachment.Status.FINALIZING
    assert attachment.finalization_token == active_claim_token
    assert storage.inspect_requests == []


def test_disabled_media_service_returns_503_without_writing(participant_pair, settings):
    settings.MEDIA_UPLOADS_AVAILABLE = False
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        reverse("api-v1:media-upload-list"),
        _score_image_payload(),
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=503,
        error_type="SERVER",
        error_code="MEDIA_UPLOADS_UNAVAILABLE",
    )
    assert not MediaAttachment.objects.exists()


def test_score_change_with_media_is_rejected_when_media_service_is_disabled(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = False
    client, csrf_token = _participant_client(participant_pair.first)

    response = _post_json(
        client,
        reverse("api-v1:score-change-list"),
        {"delta": 1, "mediaUploadIds": [str(uuid4())]},
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=503,
        error_type="SERVER",
        error_code="MEDIA_UPLOADS_UNAVAILABLE",
    )
    participant_pair.first_to_second.refresh_from_db()
    assert participant_pair.first_to_second.current_score == 0
    assert not ScoreChange.objects.exists()


def test_comment_with_media_is_rejected_when_media_service_is_disabled(
    participant_pair,
    settings,
):
    settings.MEDIA_UPLOADS_AVAILABLE = False
    change = _create_change(participant_pair)
    client, csrf_token = _participant_client(participant_pair.second)

    response = _post_json(
        client,
        reverse(
            "api-v1:score-change-comment-list",
            kwargs={"score_change_id": change.pk},
        ),
        {"mediaUploadIds": [str(uuid4())]},
        csrf_token=csrf_token,
    )

    _assert_error(
        response,
        status_code=503,
        error_type="SERVER",
        error_code="MEDIA_UPLOADS_UNAVAILABLE",
    )
    assert not ScoreChangeComment.objects.filter(score_change=change).exists()
