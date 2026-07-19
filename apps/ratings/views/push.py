import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from ..services import (
    is_valid_firebase_installation_id,
    push_devices,
    register_participant_push_device,
    unregister_participant_push_device,
)
from ._participants import get_current_participant

FID_PATTERN = push_devices.FIREBASE_INSTALLATION_ID_PATTERN
MAX_PUSH_DEVICES_PER_PARTICIPANT = push_devices.MAX_PUSH_DEVICES_PER_PARTICIPANT


def _fid_from_json_request(request):
    if request.content_type != "application/json":
        return None, JsonResponse(
            {"ok": False, "error": "application/json 요청만 지원합니다."},
            status=415,
        )

    if len(request.body) > 4096:
        return None, JsonResponse(
            {"ok": False, "error": "요청이 너무 큽니다."},
            status=400,
        )

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse(
            {"ok": False, "error": "올바른 JSON을 입력해 주세요."},
            status=400,
        )

    fid = payload.get("fid") if isinstance(payload, dict) else None
    if not is_valid_firebase_installation_id(fid):
        return None, JsonResponse(
            {"ok": False, "error": "올바른 Firebase 기기 ID가 필요합니다."},
            status=400,
        )

    return fid, None


@login_required
@require_POST
def register_push_device(request):
    participant = get_current_participant(request)
    fid, error_response = _fid_from_json_request(request)
    if error_response is not None:
        return error_response
    assert fid is not None

    result = register_participant_push_device(
        participant=participant,
        firebase_installation_id=fid,
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return JsonResponse(
        {"ok": True, "registered": True},
        status=201 if result.device_created else 200,
    )


@login_required
@require_POST
def unregister_push_device(request):
    participant = get_current_participant(request)
    fid, error_response = _fid_from_json_request(request)
    if error_response is not None:
        return error_response
    assert fid is not None

    unregister_participant_push_device(
        participant=participant,
        firebase_installation_id=fid,
    )
    return JsonResponse({"ok": True, "registered": False})


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
