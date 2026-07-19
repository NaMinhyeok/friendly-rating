from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

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
    }


@login_required
def home(request):
    get_current_participant(request)
    return render(request, "ratings/home.html", get_dashboard_context())


@login_required
def history_view(request):
    participant = get_current_participant(request)
    changes = ScoreChange.objects.select_related(
        "changed_by",
        "relationship_score__source_participant",
        "relationship_score__target_participant",
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
