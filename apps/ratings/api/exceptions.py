import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAcceptable,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.response import Response

from .contracts import ErrorCode, ErrorDetailPayload, ErrorType, error_envelope

logger = logging.getLogger(__name__)

_VALIDATION_CODE_ALIASES = {
    "invalid": "INVALID_TYPE",
    "null": "INVALID_TYPE",
}


class ApiProblem(APIException):
    status_code = 400
    default_detail = "요청을 처리할 수 없습니다."
    default_code = "api_problem"
    error_type: ClassVar[ErrorType] = ErrorType.REQUEST
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_FAILED
    reason: ClassVar[str] = "요청을 처리할 수 없습니다."

    def __init__(self, *, reason: str | None = None):
        self.api_reason = reason or self.reason
        super().__init__(detail=self.api_reason, code=self.default_code)


class CsrfFailed(ApiProblem):
    status_code = 403
    default_code = "csrf_failed"
    error_type = ErrorType.AUTHENTICATION
    error_code = ErrorCode.CSRF_FAILED
    reason = "요청의 CSRF 토큰이 올바르지 않습니다."


class ParticipantRequired(ApiProblem):
    status_code = 403
    default_code = "participant_required"
    error_type = ErrorType.AUTHORIZATION
    error_code = ErrorCode.PARTICIPANT_REQUIRED
    reason = "참가자만 이용할 수 있습니다."


class ScoreOutOfRange(ApiProblem):
    status_code = 409
    default_code = "score_out_of_range"
    error_type = ErrorType.CONFLICT
    error_code = ErrorCode.SCORE_OUT_OF_RANGE
    reason = "점수 변경 결과는 0점 이상 100점 이하여야 합니다."


class ScoreUnchanged(ApiProblem):
    status_code = 409
    default_code = "score_unchanged"
    error_type = ErrorType.CONFLICT
    error_code = ErrorCode.SCORE_UNCHANGED
    reason = "최종 점수는 현재 점수와 달라야 합니다."


class RequestBodyTooLarge(ApiProblem):
    status_code = 413
    default_code = "request_body_too_large"
    error_type = ErrorType.REQUEST
    error_code = ErrorCode.REQUEST_BODY_TOO_LARGE
    reason = "요청 본문은 4KiB 이하여야 합니다."


def _camelize_field_part(value: str) -> str:
    return re.sub(r"_([a-z])", lambda match: match.group(1).upper(), value)


def _field_path(path: tuple[str, ...]) -> str | None:
    if not path or path == ("non_field_errors",):
        return None
    return ".".join(_camelize_field_part(part) for part in path)


def _collect_validation_details(
    node: object,
    *,
    path: tuple[str, ...],
    output: list[ErrorDetailPayload],
) -> None:
    if isinstance(node, Mapping):
        message = node.get("message")
        code = node.get("code")
        if message is not None and code is not None:
            output.append(
                {
                    "field": _field_path(path),
                    "code": _VALIDATION_CODE_ALIASES.get(
                        str(code),
                        str(code).upper(),
                    ),
                    "message": str(message),
                }
            )
            return
        for key, value in node.items():
            _collect_validation_details(
                value,
                path=(*path, str(key)),
                output=output,
            )
        return
    if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
        for index, value in enumerate(node):
            child_path = path if len(node) == 1 else (*path, str(index))
            _collect_validation_details(value, path=child_path, output=output)


def _validation_details(error: ValidationError) -> list[ErrorDetailPayload]:
    output: list[ErrorDetailPayload] = []
    _collect_validation_details(error.get_full_details(), path=(), output=output)
    return output


def _error_contract(
    exception: Exception,
) -> tuple[ErrorType, ErrorCode, str, list[ErrorDetailPayload]]:
    if isinstance(exception, ApiProblem):
        return (
            exception.error_type,
            exception.error_code,
            exception.api_reason,
            [],
        )
    if isinstance(exception, ValidationError):
        return (
            ErrorType.VALIDATION,
            ErrorCode.INVALID_INPUT,
            "입력값을 확인해 주세요.",
            _validation_details(exception),
        )
    if isinstance(exception, NotAuthenticated):
        return (
            ErrorType.AUTHENTICATION,
            ErrorCode.AUTHENTICATION_REQUIRED,
            "로그인이 필요합니다.",
            [],
        )
    if isinstance(exception, AuthenticationFailed):
        return (
            ErrorType.AUTHENTICATION,
            ErrorCode.AUTHENTICATION_FAILED,
            "인증에 실패했습니다.",
            [],
        )
    if isinstance(exception, (PermissionDenied, DjangoPermissionDenied)):
        return (
            ErrorType.AUTHORIZATION,
            ErrorCode.PERMISSION_DENIED,
            "이 작업을 수행할 권한이 없습니다.",
            [],
        )
    if isinstance(exception, ParseError):
        return (
            ErrorType.REQUEST,
            ErrorCode.INVALID_JSON,
            "올바른 JSON 요청 본문을 입력해 주세요.",
            [],
        )
    if isinstance(exception, UnsupportedMediaType):
        return (
            ErrorType.REQUEST,
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "application/json 요청만 지원합니다.",
            [],
        )
    if isinstance(exception, MethodNotAllowed):
        return (
            ErrorType.REQUEST,
            ErrorCode.METHOD_NOT_ALLOWED,
            "지원하지 않는 HTTP 메서드입니다.",
            [],
        )
    if isinstance(exception, NotAcceptable):
        return (
            ErrorType.REQUEST,
            ErrorCode.NOT_ACCEPTABLE,
            "application/json 응답만 지원합니다.",
            [],
        )
    if isinstance(exception, (NotFound, Http404)):
        return (
            ErrorType.NOT_FOUND,
            ErrorCode.NOT_FOUND,
            "요청한 리소스를 찾을 수 없습니다.",
            [],
        )
    if isinstance(exception, Throttled):
        return (
            ErrorType.RATE_LIMIT,
            ErrorCode.RATE_LIMITED,
            "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            [],
        )
    return (
        ErrorType.REQUEST,
        ErrorCode.REQUEST_FAILED,
        "요청을 처리할 수 없습니다.",
        [],
    )


def api_exception_handler(
    exception: Exception,
    context: dict[str, Any],
) -> Response | None:
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exception, context)
    if response is None:
        if settings.DEBUG:
            return None
        from rest_framework.views import set_rollback

        set_rollback()
        logger.error(
            "Unhandled exception at the API boundary.",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return Response(
            error_envelope(
                error_type=ErrorType.SERVER,
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                reason="서버에서 요청을 처리하지 못했습니다.",
            ),
            status=500,
        )

    if response.status_code >= 500 and not isinstance(exception, ApiProblem):
        response.data = error_envelope(
            error_type=ErrorType.SERVER,
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            reason="서버에서 요청을 처리하지 못했습니다.",
        )
        return response

    error_type, error_code, reason, details = _error_contract(exception)
    response.data = error_envelope(
        error_type=error_type,
        error_code=error_code,
        reason=reason,
        details=details,
    )
    return response
