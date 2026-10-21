"""Adresy powiadomień panelu uczestnika – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("me/notifications/", views.NotificationListView.as_view(), name="participant-notifications"),
    path(
        "me/notifications/read-all/",
        views.NotificationReadAllView.as_view(),
        name="participant-notifications-read-all",
    ),
    path(
        "me/notifications/<int:pk>/",
        views.NotificationOpenView.as_view(),
        name="participant-notification-open",
    ),
]
