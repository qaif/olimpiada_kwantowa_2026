"""Adresy statystyk szkół (STAT-01) – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web:``).

Wzorce stoją w mapie zawsze; o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404 przy
wyłączonej fladze ``school_statistics``) – ten sam układ, co ``apps/web/urls_student_status.py``.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path(
        "supervisor/statistics/",
        views.SupervisorStatisticsView.as_view(),
        name="supervisor-statistics",
    ),
    path(
        "supervisor/statistics/report.pdf",
        views.SupervisorReportView.as_view(),
        name="supervisor-statistics-report",
    ),
    path(
        "coordinator/school-stats/",
        views.CoordinatorStatisticsView.as_view(),
        name="coordinator-school-stats",
    ),
    path(
        "coordinator/school-stats/export.csv",
        views.CoordinatorExportView.as_view(),
        name="coordinator-school-stats-export",
    ),
    path(
        "coordinator/school-stats/schools/<int:school_id>/report.pdf",
        views.CoordinatorReportView.as_view(),
        name="coordinator-school-stats-report",
    ),
]
