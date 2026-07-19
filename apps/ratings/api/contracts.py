from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TypedDict, TypeGuard


class ResultType(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class ErrorType(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    VALIDATION = "VALIDATION"
    REQUEST = "REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER = "SERVER"


class ErrorCode(StrEnum):
    REQUEST_FAILED = "REQUEST_FAILED"
    INVALID_JSON = "INVALID_JSON"
    INVALID_INPUT = "INVALID_INPUT"
    REQUEST_BODY_TOO_LARGE = "REQUEST_BODY_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    NOT_ACCEPTABLE = "NOT_ACCEPTABLE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CSRF_FAILED = "CSRF_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PARTICIPANT_REQUIRED = "PARTICIPANT_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    SCORE_OUT_OF_RANGE = "SCORE_OUT_OF_RANGE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


class ErrorDetailPayload(TypedDict):
    field: str | None
    code: str
    message: str


class ApiErrorPayload(TypedDict):
    errorType: str
    errorCode: str
    reason: str
    details: list[ErrorDetailPayload]


class SuccessEnvelope(TypedDict):
    resultType: str
    error: None
    success: object


class ErrorEnvelope(TypedDict):
    resultType: str
    error: ApiErrorPayload
    success: None


def success_envelope(data: object) -> SuccessEnvelope:
    return {
        "resultType": ResultType.SUCCESS.value,
        "error": None,
        "success": data,
    }


def error_envelope(
    *,
    error_type: ErrorType,
    error_code: ErrorCode,
    reason: str,
    details: Sequence[ErrorDetailPayload] = (),
) -> ErrorEnvelope:
    return {
        "resultType": ResultType.ERROR.value,
        "error": {
            "errorType": error_type.value,
            "errorCode": error_code.value,
            "reason": reason,
            "details": list(details),
        },
        "success": None,
    }


def is_success_envelope(value: object) -> TypeGuard[SuccessEnvelope]:
    if not isinstance(value, Mapping):
        return False
    return (
        value.get("resultType") == ResultType.SUCCESS.value
        and "error" in value
        and value.get("error") is None
        and "success" in value
    )


def is_error_envelope(value: object) -> TypeGuard[ErrorEnvelope]:
    if not isinstance(value, Mapping):
        return False
    return (
        value.get("resultType") == ResultType.ERROR.value
        and isinstance(value.get("error"), Mapping)
        and value.get("success", object()) is None
    )
