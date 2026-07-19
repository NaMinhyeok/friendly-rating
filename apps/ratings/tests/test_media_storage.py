from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from ..media_storage import MediaStorageOperationError, R2MediaStorageGateway


@dataclass
class FailingStreamingBody:
    closed: bool = False

    def read(self, amount: int | None = None) -> bytes:
        raise OSError("incomplete response")

    def close(self) -> None:
        self.closed = True


@dataclass
class RecordingS3Client:
    presigned_requests: list[tuple[str, dict[str, object], int]] = field(
        default_factory=list
    )
    copy_requests: list[dict[str, object]] = field(default_factory=list)

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, object],
        ExpiresIn: int,
    ) -> str:
        self.presigned_requests.append((client_method, dict(Params), ExpiresIn))
        return f"https://r2.example.test/{client_method}"

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        raise AssertionError("Object inspection is outside this signing test.")

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Range: str,
    ) -> Mapping[str, object]:
        raise AssertionError("Object inspection is outside this signing test.")

    def copy_object(
        self,
        *,
        Bucket: str,
        Key: str,
        CopySource: Mapping[str, str],
        MetadataDirective: str,
        ContentType: str,
        CacheControl: str,
        ContentDisposition: str,
    ) -> Mapping[str, object]:
        self.copy_requests.append(
            {
                "Bucket": Bucket,
                "Key": Key,
                "CopySource": dict(CopySource),
                "MetadataDirective": MetadataDirective,
                "ContentType": ContentType,
                "CacheControl": CacheControl,
                "ContentDisposition": ContentDisposition,
            }
        )
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        raise AssertionError("Object deletion is outside this signing test.")


@dataclass
class InspectionS3Client(RecordingS3Client):
    body: FailingStreamingBody = field(default_factory=FailingStreamingBody)

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        return {
            "ContentLength": 512,
            "ContentType": "image/jpeg",
            "ETag": '"test-etag"',
        }

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Range: str,
    ) -> Mapping[str, object]:
        return {"Body": self.body}


def _gateway(settings) -> tuple[R2MediaStorageGateway, RecordingS3Client]:
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client = RecordingS3Client()
    gateway = R2MediaStorageGateway(
        client=client,
        bucket_name="private-media",
    )
    return gateway, client


def test_upload_signing_binds_content_length_and_private_cache_control(settings):
    gateway, client = _gateway(settings)

    url = gateway.generate_upload_url(
        object_key="pending/upload-id",
        content_type="image/jpeg",
        content_length=512,
        expires_in=120,
    )

    assert url == "https://r2.example.test/put_object"
    assert client.presigned_requests == [
        (
            "put_object",
            {
                "Bucket": "private-media",
                "Key": "pending/upload-id",
                "ContentType": "image/jpeg",
                "ContentLength": 512,
                "CacheControl": "private, no-store, max-age=0",
            },
            120,
        )
    ]


def test_object_inspection_closes_the_response_body_when_reading_fails(settings):
    settings.MEDIA_UPLOADS_AVAILABLE = True
    client = InspectionS3Client()
    gateway = R2MediaStorageGateway(client=client, bucket_name="private-media")

    with pytest.raises(MediaStorageOperationError):
        gateway.inspect_object(object_key="pending/upload-id")

    assert client.body.closed is True


def test_object_promotion_replaces_metadata_with_private_media_headers(settings):
    gateway, client = _gateway(settings)

    gateway.promote_object(
        source_key="pending/upload-id",
        destination_key="media/upload-id/claim-token",
        content_type="image/jpeg",
        original_name="오늘 사진.jpg",
    )

    assert client.copy_requests == [
        {
            "Bucket": "private-media",
            "Key": "media/upload-id/claim-token",
            "CopySource": {
                "Bucket": "private-media",
                "Key": "pending/upload-id",
            },
            "MetadataDirective": "REPLACE",
            "ContentType": "image/jpeg",
            "CacheControl": "private, no-store, max-age=0",
            "ContentDisposition": (
                "inline; filename*=UTF-8''%EC%98%A4%EB%8A%98%20%EC%82%AC%EC%A7%84.jpg"
            ),
        }
    ]


def test_download_signing_uses_only_the_private_object_identity(settings):
    gateway, client = _gateway(settings)

    url = gateway.generate_download_url(
        object_key="media/upload-id/claim-token",
        content_type="image/jpeg",
        original_name="오늘 사진.jpg",
        expires_in=180,
    )

    assert url == "https://r2.example.test/get_object"
    assert client.presigned_requests == [
        (
            "get_object",
            {
                "Bucket": "private-media",
                "Key": "media/upload-id/claim-token",
            },
            180,
        )
    ]
