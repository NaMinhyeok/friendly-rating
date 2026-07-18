from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import PinLoginForm, ScoreChangeForm
from .models import Participant, RelationshipScore, ScoreChange
from .services import change_relationship_score


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


def health(request):
    return HttpResponse("ok", content_type="text/plain")
