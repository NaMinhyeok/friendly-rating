from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ..forms import ScoreChangeForm
from ..models import RelationshipScore, ScoreChange
from ..services import change_relationship_score
from ._participants import get_current_participant


def get_dashboard_context(participant, *, score_form=None):
    scores = list(
        RelationshipScore.objects.select_related(
            "source_participant", "target_participant"
        ).order_by("source_participant__slot")
    )
    scores.sort(key=lambda score: score.source_participant_id != participant.pk)
    own_score = next(
        score for score in scores if score.source_participant_id == participant.pk
    )
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


@login_required
@require_POST
def change_score_view(request):
    participant = get_current_participant(request)
    form = ScoreChangeForm(request.POST)
    if form.is_valid():
        try:
            change = change_relationship_score(
                source_participant=participant,
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
