from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import quote

from django.conf import settings

INITIAL_SIGNATURE_BYTES = 4096
PRIVATE_MEDIA_CACHE_CONTROL = "private, no-store, max-age=0"


class MediaStorageError(RuntimeError):
    """Base error for the private object-storage boundary."""


class MediaStorageConfigurationError(MediaStorageError):
    """Raised when the R2 gateway cannot be configured."""


class MediaStorageOperationError(MediaStorageError):
    """Raised when an R2 operation fails."""


class MediaObjectNotFoundError(MediaStorageOperationError):
    """Raised when an expected pending R2 object does not exist."""


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    size: int
    content_type: str
    etag: str
    initial_bytes: bytes


class MediaStorageGateway(Protocol):
    def generate_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str: ...

    def inspect_object(self, *, object_key: str) -> StoredMediaObject: ...

    def promote_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
        original_name: str,
    ) -> None: ...

    def delete_object(self, *, object_key: str) -> None: ...

    def generate_download_url(
        self,
        *,
        object_key: str,
        content_type: str,
        original_name: str,
        expires_in: int,
    ) -> str: ...


class _StreamingBody(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...


class _S3Client(Protocol):
    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, object],
        ExpiresIn: int,
    ) -> str: ...

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Range: str,
    ) -> Mapping[str, object]: ...

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
    ) -> Mapping[str, object]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...


class _Boto3Module(Protocol):
    def client(
        self,
        service_name: str,
        *,
        endpoint_url: str,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        region_name: str,
        config: object,
    ) -> object: ...


def _required_string_setting(name: str) -> str:
    value = getattr(settings, name, "")
    if not isinstance(value, str) or not value.strip():
        raise MediaStorageConfigurationError(f"{name} is not configured.")
    return value.strip()


def _create_r2_client() -> _S3Client:
    try:
        boto3_module = cast(_Boto3Module, importlib.import_module("boto3"))
        config_module = importlib.import_module("botocore.config")
        config_class = config_module.Config
        client_config = config_class(
            signature_version="s3v4",
            connect_timeout=2,
            read_timeout=5,
            retries={"total_max_attempts": 1, "mode": "standard"},
        )
    except (ImportError, AttributeError) as error:
        raise MediaStorageConfigurationError(
            "The boto3 and botocore packages are required for R2 media storage."
        ) from error

    try:
        client = boto3_module.client(
            "s3",
            endpoint_url=_required_string_setting("R2_ENDPOINT_URL"),
            aws_access_key_id=_required_string_setting("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=_required_string_setting("R2_SECRET_ACCESS_KEY"),
            region_name=str(getattr(settings, "R2_REGION_NAME", "auto")),
            config=client_config,
        )
    except MediaStorageError:
        raise
    except Exception as error:
        raise MediaStorageConfigurationError(
            "Could not configure the R2 media storage client."
        ) from error
    return cast(_S3Client, client)


def _is_missing_object_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    error_details = response.get("Error")
    if not isinstance(error_details, Mapping):
        return False
    code = error_details.get("Code")
    return code in {"404", "NoSuchKey", "NotFound"}


def _response_int(response: Mapping[str, object], key: str) -> int:
    value = response.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MediaStorageOperationError(
            f"R2 returned an invalid {key} value for a media object."
        )
    return value


def _response_string(response: Mapping[str, object], key: str) -> str:
    value = response.get(key)
    if not isinstance(value, str):
        return ""
    return value


class R2MediaStorageGateway:
    def __init__(
        self,
        *,
        client: _S3Client | None = None,
        bucket_name: str | None = None,
    ) -> None:
        if not getattr(settings, "MEDIA_UPLOADS_AVAILABLE", False):
            raise MediaStorageConfigurationError(
                "Private media uploads are not configured."
            )
        self._client = client if client is not None else _create_r2_client()
        self._bucket_name = (
            bucket_name
            if bucket_name is not None
            else _required_string_setting("R2_BUCKET_NAME")
        )
        if not self._bucket_name:
            raise MediaStorageConfigurationError("R2_BUCKET_NAME is not configured.")

    def generate_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        expires_in: int,
    ) -> str:
        try:
            return self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket_name,
                    "Key": object_key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                    "CacheControl": PRIVATE_MEDIA_CACHE_CONTROL,
                },
                ExpiresIn=expires_in,
            )
        except Exception as error:
            raise MediaStorageOperationError(
                "Could not create an R2 upload URL."
            ) from error

    def inspect_object(self, *, object_key: str) -> StoredMediaObject:
        try:
            head_response = self._client.head_object(
                Bucket=self._bucket_name,
                Key=object_key,
            )
            range_response = self._client.get_object(
                Bucket=self._bucket_name,
                Key=object_key,
                Range=f"bytes=0-{INITIAL_SIGNATURE_BYTES - 1}",
            )
            body_value = range_response.get("Body")
            if body_value is None or not hasattr(body_value, "read"):
                raise MediaStorageOperationError(
                    "R2 returned an invalid media object body."
                )
            body = cast(_StreamingBody, body_value)
            try:
                initial_bytes = body.read(INITIAL_SIGNATURE_BYTES)
            finally:
                close_body = getattr(body_value, "close", None)
                if callable(close_body):
                    close_body()
        except MediaStorageError:
            raise
        except Exception as error:
            if _is_missing_object_error(error):
                raise MediaObjectNotFoundError(
                    "The pending R2 media object does not exist."
                ) from error
            raise MediaStorageOperationError(
                "Could not inspect the pending R2 media object."
            ) from error

        if not isinstance(initial_bytes, bytes):
            raise MediaStorageOperationError(
                "R2 returned non-byte media object content."
            )
        return StoredMediaObject(
            size=_response_int(head_response, "ContentLength"),
            content_type=_response_string(head_response, "ContentType")
            .partition(";")[0]
            .strip()
            .lower(),
            etag=_response_string(head_response, "ETag").strip('"'),
            initial_bytes=initial_bytes,
        )

    def promote_object(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
        original_name: str,
    ) -> None:
        encoded_name = quote(original_name, safe="")
        try:
            self._client.copy_object(
                Bucket=self._bucket_name,
                Key=destination_key,
                CopySource={"Bucket": self._bucket_name, "Key": source_key},
                MetadataDirective="REPLACE",
                ContentType=content_type,
                CacheControl=PRIVATE_MEDIA_CACHE_CONTROL,
                ContentDisposition=f"inline; filename*=UTF-8''{encoded_name}",
            )
        except Exception as error:
            raise MediaStorageOperationError(
                "Could not copy the validated R2 media object."
            ) from error

    def delete_object(self, *, object_key: str) -> None:
        try:
            self._client.delete_object(
                Bucket=self._bucket_name,
                Key=object_key,
            )
        except Exception as error:
            raise MediaStorageOperationError(
                "Could not delete an R2 media object."
            ) from error

    def generate_download_url(
        self,
        *,
        object_key: str,
        content_type: str,
        original_name: str,
        expires_in: int,
    ) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket_name,
                    "Key": object_key,
                },
                ExpiresIn=expires_in,
            )
        except Exception as error:
            raise MediaStorageOperationError(
                "Could not create an R2 download URL."
            ) from error


def get_media_storage_gateway() -> MediaStorageGateway:
    return R2MediaStorageGateway()


__all__ = (
    "INITIAL_SIGNATURE_BYTES",
    "MediaObjectNotFoundError",
    "MediaStorageConfigurationError",
    "MediaStorageError",
    "MediaStorageGateway",
    "MediaStorageOperationError",
    "PRIVATE_MEDIA_CACHE_CONTROL",
    "R2MediaStorageGateway",
    "StoredMediaObject",
    "get_media_storage_gateway",
)
