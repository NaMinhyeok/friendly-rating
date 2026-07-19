from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("history/", views.history_view, name="history"),
    path(
        "history/<int:score_change_id>/",
        views.score_change_thread_view,
        name="score-change-thread",
    ),
    path(
        "media/<uuid:attachment_id>/content/",
        views.media_content,
        name="media-content",
    ),
    path("service-worker.js", views.service_worker, name="service-worker"),
    path("health/", views.health, name="health"),
]
