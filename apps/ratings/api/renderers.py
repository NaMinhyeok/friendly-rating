from collections.abc import Mapping
from typing import Any, cast, override

from django.http.response import HttpResponseBase
from rest_framework.renderers import JSONRenderer

from .contracts import (
    ErrorCode,
    ErrorType,
    error_envelope,
    is_error_envelope,
    is_success_envelope,
    success_envelope,
)


class EnvelopeJSONRenderer(JSONRenderer):
    @override
    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        response = renderer_context.get("response") if renderer_context else None
        status_code = (
            response.status_code if isinstance(response, HttpResponseBase) else 200
        )

        if status_code >= 400 and is_error_envelope(data):
            payload: object = data
        elif status_code < 400 and is_success_envelope(data):
            payload: object = data
        elif status_code >= 400:
            payload = error_envelope(
                error_type=(
                    ErrorType.SERVER if status_code >= 500 else ErrorType.REQUEST
                ),
                error_code=(
                    ErrorCode.INTERNAL_SERVER_ERROR
                    if status_code >= 500
                    else ErrorCode.REQUEST_FAILED
                ),
                reason=(
                    "서버에서 요청을 처리하지 못했습니다."
                    if status_code >= 500
                    else "요청을 처리할 수 없습니다."
                ),
            )
        else:
            payload = success_envelope(data)

        return cast(
            bytes,
            super().render(payload, accepted_media_type, renderer_context),
        )
