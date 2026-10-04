"""Adresy sieci absolwentów (ALUM-01) – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web``).

Wzorce stoją w mapie zawsze; o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404 przy
wyłączonej fladze ``alumni``) – ta sama umowa, co ``urls_student_status``. ``alumni`` w adresie, a nie
„absolwenci”: słowo działa w obu językach konkursów (Olimpiada Kwantowa i anglojęzyczna IQO).
Identyfikatory osób w adresach to losowe tokeny profilu, nie ``pk`` uczestnika ani kod publiczny.
"""

from __future__ import annotations

from django.urls import path

from . import views, views_coordinator

urlpatterns = [
    path("alumni/", views.AlumniWallView.as_view(), name="alumni-wall"),
    path("alumni/unsubscribe/<str:token>/", views.AlumniUnsubscribeView.as_view(), name="alumni-unsubscribe"),
    path("me/alumni/", views.AlumniHomeView.as_view(), name="alumni"),
    path("me/alumni/join/", views.AlumniJoinView.as_view(), name="alumni-join"),
    path("me/alumni/profile/", views.AlumniProfileView.as_view(), name="alumni-profile"),
    path("me/alumni/withdraw/", views.AlumniWithdrawView.as_view(), name="alumni-withdraw"),
    path("me/alumni/directory/", views.AlumniDirectoryView.as_view(), name="alumni-directory"),
    path("me/alumni/mentor/<str:token>/", views.AlumniRequestView.as_view(), name="alumni-request"),
    path(
        "me/alumni/mentoring/<int:pk>/flag/",
        views.AlumniFlagView.as_view(),
        name="alumni-flag",
    ),
    path(
        "me/alumni/mentoring/<int:pk>/<slug:action>/",
        views.AlumniMentoringActionView.as_view(),
        name="alumni-mentoring-action",
    ),
    path("coordinator/alumni/", views_coordinator.CoordinatorAlumniView.as_view(), name="coordinator-alumni"),
    path(
        "coordinator/alumni/<int:pk>/hide/",
        views_coordinator.CoordinatorAlumniHideView.as_view(),
        name="coordinator-alumni-hide",
    ),
    path(
        "coordinator/alumni/mentoring/",
        views_coordinator.CoordinatorMentoringView.as_view(),
        name="coordinator-alumni-mentoring",
    ),
    path(
        "coordinator/alumni/mentoring/<int:pk>/end/",
        views_coordinator.CoordinatorMentoringEndView.as_view(),
        name="coordinator-alumni-end",
    ),
    path(
        "coordinator/alumni/flags/<int:pk>/resolve/",
        views_coordinator.CoordinatorFlagResolveView.as_view(),
        name="coordinator-alumni-flag-resolve",
    ),
    path(
        "coordinator/alumni/invitations/",
        views_coordinator.CoordinatorInvitationsView.as_view(),
        name="coordinator-alumni-invitations",
    ),
    path(
        "coordinator/alumni/stats/",
        views_coordinator.CoordinatorStatsView.as_view(),
        name="coordinator-alumni-stats",
    ),
]
