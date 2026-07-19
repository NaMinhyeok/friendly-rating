import json
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import Participant, PushDevice
from ._participants import get_current_participant

FID_PATTERN = re.compile(r"^[cdef][A-Za-z0-9_-]{21}$")
MAX_PUSH_DEVICES_PER_PARTICIPANT = 5


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
    if not isinstance(fid, str) or not FID_PATTERN.fullmatch(fid):
        return None, JsonResponse(
            {"ok": False, "error": "올바른 Firebase 기기 ID가 필요합니다."},
            status=400,
        )

    return fid, None


@login_required
@require_POST
@transaction.atomic
def register_push_device(request):
    participant = get_current_participant(request)
    fid, error_response = _fid_from_json_request(request)
    if error_response is not None:
        return error_response

    Participant.objects.select_for_update().get(pk=participant.pk)
    _, created = PushDevice.objects.update_or_create(
        firebase_installation_id=fid,
        defaults={
            "participant": participant,
            "is_active": True,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
        },
    )
    PushDevice.objects.filter(
        participant=participant,
        is_active=False,
    ).delete()
    retained_active_ids = list(
        PushDevice.objects.filter(participant=participant, is_active=True)
        .order_by("-updated_at", "-pk")
        .values_list("pk", flat=True)[:MAX_PUSH_DEVICES_PER_PARTICIPANT]
    )
    PushDevice.objects.filter(participant=participant, is_active=True).exclude(
        pk__in=retained_active_ids
    ).delete()
    return JsonResponse(
        {"ok": True, "registered": True},
        status=201 if created else 200,
    )


@login_required
@require_POST
def unregister_push_device(request):
    participant = get_current_participant(request)
    fid, error_response = _fid_from_json_request(request)
    if error_response is not None:
        return error_response

    PushDevice.objects.filter(
        participant=participant,
        firebase_installation_id=fid,
    ).update(
        is_active=False,
        updated_at=timezone.now(),
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
