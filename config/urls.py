from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularJSONAPIView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularJSONAPIView.as_view(), name="api-schema"),
    path(
        "api/v1/",
        include(("apps.ratings.api.urls", "ratings-api"), namespace="api-v1"),
    ),
    path("", include("apps.ratings.urls")),
]
