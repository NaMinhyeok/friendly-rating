from django.urls import path

from .views import (
    ApiNotFoundView,
    MediaUploadCompleteView,
    MediaUploadInitiateView,
    PushDeviceRegisterView,
    PushDeviceUnregisterView,
    RelationshipScoreListView,
    ScoreChangeCommentCreateView,
    ScoreChangeDetailView,
    ScoreChangeListView,
)

app_name = "ratings-api"

urlpatterns = [
    path(
        "media-uploads/",
        MediaUploadInitiateView.as_view(),
        name="media-upload-list",
    ),
    path(
        "media-uploads/<uuid:upload_id>/complete/",
        MediaUploadCompleteView.as_view(),
        name="media-upload-complete",
    ),
    path(
        "relationship-scores/",
        RelationshipScoreListView.as_view(),
        name="relationship-score-list",
    ),
    path(
        "score-changes/",
        ScoreChangeListView.as_view(),
        name="score-change-list",
    ),
    path(
        "score-changes/<int:score_change_id>/",
        ScoreChangeDetailView.as_view(),
        name="score-change-detail",
    ),
    path(
        "score-changes/<int:score_change_id>/comments/",
        ScoreChangeCommentCreateView.as_view(),
        name="score-change-comment-list",
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
