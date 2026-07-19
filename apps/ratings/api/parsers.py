from collections.abc import Mapping
from io import BytesIO
from typing import IO, Any, override

from rest_framework.parsers import JSONParser

from .exceptions import RequestBodyTooLarge

MAX_JSON_BODY_BYTES = 4 * 1024


class BoundedJSONParser(JSONParser):
    @override
    def parse(
        self,
        stream: IO[Any],
        media_type: str | None = None,
        parser_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        content_length = None
        if parser_context is not None:
            request = parser_context.get("request")
            if request is not None:
                content_length = request.META.get("CONTENT_LENGTH")

        if content_length:
            try:
                if int(content_length) > MAX_JSON_BODY_BYTES:
                    raise RequestBodyTooLarge()
            except ValueError:
                pass

        body = stream.read(MAX_JSON_BODY_BYTES + 1)
        if len(body) > MAX_JSON_BODY_BYTES:
            raise RequestBodyTooLarge()

        return super().parse(
            BytesIO(body),
            media_type=media_type,
            parser_context=parser_context,
        )
