from django.conf import settings
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..forms import PinLoginForm


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
