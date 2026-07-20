from django.urls import path

from .views import (
    ApiNotFoundView,
    DiaryEntryCommentCreateView,
    DiaryEntryDetailView,
    DiaryEntryListView,
    MediaUploadCompleteView,
    MediaUploadDiscardView,
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
        "diary-entries/",
        DiaryEntryListView.as_view(),
        name="diary-entry-list",
    ),
    path(
        "diary-entries/<int:diary_entry_id>/",
        DiaryEntryDetailView.as_view(),
        name="diary-entry-detail",
    ),
    path(
        "diary-entries/<int:diary_entry_id>/comments/",
        DiaryEntryCommentCreateView.as_view(),
        name="diary-entry-comment-list",
    ),
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
        "media-uploads/<uuid:upload_id>/discard/",
        MediaUploadDiscardView.as_view(),
        name="media-upload-discard",
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
