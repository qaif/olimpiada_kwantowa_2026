"""Adresy panelu nauczyciela (UX-PANEL-02) – rozwijane w ``apps/web/urls.py`` (przestrzeń ``web``) zaraz
po pozostałych adresach ``supervisor/…``. ``/supervisor/`` (Start) zostaje tam, gdzie był."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("supervisor/progress/", views.ClassProgressView.as_view(), name="supervisor-progress"),
    path(
        "supervisor/progress/export.<str:fmt>",
        views.ClassProgressExportView.as_view(),
        name="supervisor-progress-export",
    ),
    path("supervisor/school/", views.SchoolView.as_view(), name="supervisor-school"),
]
