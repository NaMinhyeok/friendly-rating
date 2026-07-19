from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from ..models import ScoreChange
from ._participants import get_current_participant


def get_dashboard_context():
    return {
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
        "media_uploads_available": settings.MEDIA_UPLOADS_AVAILABLE,
    }


@login_required
def home(request):
    get_current_participant(request)
    return render(request, "ratings/home.html", get_dashboard_context())


@login_required
def history_view(request):
    get_current_participant(request)
    return render(request, "ratings/history.html", get_dashboard_context())


@login_required
@require_GET
def diary_view(request):
    get_current_participant(request)
    response = render(request, "ratings/diary.html")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def score_change_thread_view(request, score_change_id: int):
    participant = get_current_participant(request)
    get_object_or_404(
        ScoreChange.objects.filter(
            Q(relationship_score__source_participant=participant)
            | Q(relationship_score__target_participant=participant),
        ),
        pk=score_change_id,
    )
    context = get_dashboard_context()
    context["score_change_id"] = score_change_id
    response = render(request, "ratings/score_change_thread.html", context)
    response.headers["Cache-Control"] = "private, no-store"
    return response
