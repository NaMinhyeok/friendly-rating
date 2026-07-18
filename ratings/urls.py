from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("score/change/", views.change_score_view, name="change-score"),
    path("history/", views.history_view, name="history"),
    path("health/", views.health, name="health"),
]
