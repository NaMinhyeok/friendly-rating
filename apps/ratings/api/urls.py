from django.urls import path

from .views import ApiNotFoundView, ScoreChangeListView

app_name = "ratings-api"

urlpatterns = [
    path(
        "score-changes/",
        ScoreChangeListView.as_view(),
        name="score-change-list",
    ),
    path("", ApiNotFoundView.as_view(), name="not-found-root"),
    path("<path:unmatched_path>", ApiNotFoundView.as_view(), name="not-found"),
]
