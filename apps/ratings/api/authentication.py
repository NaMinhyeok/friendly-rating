from typing import TYPE_CHECKING, override

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request

from .exceptions import CsrfFailed

if TYPE_CHECKING:
    from drf_spectacular.openapi import AutoSchema


class SameOriginSessionAuthentication(SessionAuthentication):
    @override
    def enforce_csrf(self, request: Request) -> None:
        try:
            super().enforce_csrf(request)
        except PermissionDenied as error:
            raise CsrfFailed() from error


class SameOriginSessionScheme(OpenApiAuthenticationExtension):
    target_class = SameOriginSessionAuthentication
    name = "cookieAuth"

    @override
    def get_security_definition(self, auto_schema: "AutoSchema") -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.SESSION_COOKIE_NAME,
        }
