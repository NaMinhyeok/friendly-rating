import json

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def service_worker(request):
    firebase_config = (
        settings.FIREBASE_WEB_CONFIG if settings.PUSH_NOTIFICATIONS_AVAILABLE else {}
    )
    response = render(
        request,
        "service-worker.js",
        {
            "firebase_config_json": json.dumps(
                firebase_config,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        },
        content_type="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
