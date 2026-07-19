from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

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
    get_current_participant(request)
    return render(request, "ratings/history.html")
