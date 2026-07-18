from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import PinLoginForm


@login_required
def home(request):
    try:
        participant = request.user.participant
    except AttributeError as error:
        raise PermissionDenied("참가자만 이용할 수 있습니다.") from error

    return render(request, "ratings/home.html", {"participant": participant})


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


def health(request):
    return HttpResponse("ok", content_type="text/plain")
