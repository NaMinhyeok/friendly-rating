import json
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import PinLoginForm, ScoreChangeForm
from .models import Participant, PushDevice, RelationshipScore, ScoreChange
from .services import change_relationship_score

FID_PATTERN = re.compile(r"^[cdef][A-Za-z0-9_-]{21}$")
MAX_PUSH_DEVICES_PER_PARTICIPANT = 5


def get_current_participant(request):
    try:
        return request.user.participant
    except Participant.DoesNotExist as error:
        raise PermissionDenied("참가자만 이용할 수 있습니다.") from error


def get_dashboard_context(participant, *, score_form=None):
    scores = list(
        RelationshipScore.objects.select_related("rater", "recipient").order_by(
            "rater__slot"
        )
    )
    scores.sort(key=lambda score: score.rater_id != participant.pk)
    own_score = next(score for score in scores if score.rater_id == participant.pk)
    return {
        "participant": participant,
        "scores": scores,
        "own_score": own_score,
        "score_form": score_form if score_form is not None else ScoreChangeForm(),
        "firebase_config": (
            settings.FIREBASE_WEB_CONFIG
            if settings.PUSH_NOTIFICATIONS_AVAILABLE
            else {}
        ),
        "firebase_vapid_key": (
            settings.FIREBASE_VAPID_PUBLIC_KEY
            if settings.PUSH_NOTIFICATIONS_AVAILABLE
            else ""
        ),
        "push_notifications_enabled": settings.PUSH_NOTIFICATIONS_AVAILABLE,
    }


@login_required
def home(request):
    participant = get_current_participant(request)
    return render(request, "ratings/home.html", get_dashboard_context(participant))


def login_view(request):
    if request.user.is_authenticated and hasattr(request.user, "participant"):
        return redirect("home")

    form = PinLoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        auth_login(request, form.authenticated_user)
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("home")

    return render(
        request,
        "ratings/login.html",
        {
            "form": form,
            "next": request.GET.get("next", ""),
        },
    )


def login_lockout(request, _original_response=None, _credentials=None):
    response = render(
        request,
        "ratings/login.html",
        {
            "form": PinLoginForm(request.POST or None, request=request),
            "next": request.POST.get("next", request.GET.get("next", "")),
            "login_rate_limited": True,
            "login_rate_limit_message": settings.AXES_COOLOFF_MESSAGE,
        },
        status=settings.AXES_HTTP_RESPONSE_CODE,
    )
    response.headers["Retry-After"] = str(
        int(settings.AXES_COOLOFF_TIME.total_seconds())
    )
    return response


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect(reverse("login"))


@login_required
@require_POST
def change_score_view(request):
    participant = get_current_participant(request)
    form = ScoreChangeForm(request.POST)
    if form.is_valid():
        try:
            change = change_relationship_score(
                rater=participant,
                delta=form.delta,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            sign = "+" if change.delta > 0 else ""
            messages.success(
                request,
                f"친밀도를 {sign}{change.delta}점 변경했어요.",
            )
            return redirect("home")

    return render(
        request,
        "ratings/home.html",
        get_dashboard_context(participant, score_form=form),
        status=400,
    )


@login_required
def history_view(request):
    participant = get_current_participant(request)
    changes = ScoreChange.objects.select_related(
        "changed_by",
        "score__rater",
        "score__recipient",
    )
    page = Paginator(changes, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "ratings/history.html",
        {
            "participant": participant,
            "page": page,
        },
    )


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
        fid=fid,
        defaults={
            "participant": participant,
            "active": True,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
        },
    )
    PushDevice.objects.filter(
        participant=participant,
        active=False,
    ).delete()
    retained_active_ids = list(
        PushDevice.objects.filter(participant=participant, active=True)
        .order_by("-updated_at", "-pk")
        .values_list("pk", flat=True)[:MAX_PUSH_DEVICES_PER_PARTICIPANT]
    )
    PushDevice.objects.filter(participant=participant, active=True).exclude(
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
        fid=fid,
    ).update(
        active=False,
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


def health(request):
    return HttpResponse("ok", content_type="text/plain")
