from django.urls import path

from .views import ScoreChangeListView

app_name = "ratings-api"

urlpatterns = [
    path(
        "score-changes/",
        ScoreChangeListView.as_view(),
        name="score-change-list",
    ),
]
