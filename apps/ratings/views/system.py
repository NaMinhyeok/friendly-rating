from django.http import HttpResponse
from django.views.decorators.http import require_GET

from ..health import database_is_ready


@require_GET
def health(request):
    if database_is_ready():
        response = HttpResponse("ok", content_type="text/plain")
    else:
        response = HttpResponse(
            "unavailable",
            content_type="text/plain",
            status=503,
        )

    response.headers["Cache-Control"] = "no-store"
    return response
