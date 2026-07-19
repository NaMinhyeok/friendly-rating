from django.urls import path

from .views import (
    ApiNotFoundView,
    PushDeviceRegisterView,
    PushDeviceUnregisterView,
    ScoreChangeListView,
)

app_name = "ratings-api"

urlpatterns = [
    path(
        "score-changes/",
        ScoreChangeListView.as_view(),
        name="score-change-list",
    ),
    path(
        "push-devices/register/",
        PushDeviceRegisterView.as_view(),
        name="push-device-register",
    ),
    path(
        "push-devices/unregister/",
        PushDeviceUnregisterView.as_view(),
        name="push-device-unregister",
    ),
    path("", ApiNotFoundView.as_view(), name="not-found-root"),
    path("<path:unmatched_path>", ApiNotFoundView.as_view(), name="not-found"),
]
