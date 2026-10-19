"""Adresy STATS-02 – doklejane do ``apps/web/urls.py`` (przestrzeń nazw ``web:``), jak MAIL-02."""

from django.urls import path

from . import views

urlpatterns = [
    path("coordinator/stats/funnel/", views.FunnelView.as_view(), name="coordinator-stats-funnel"),
    path(
        "coordinator/stats/funnel/export.csv",
        views.FunnelExportView.as_view(),
        name="coordinator-stats-funnel-export",
    ),
    path(
        "coordinator/messages/<int:pk>/report/",
        views.BroadcastReportView.as_view(),
        name="coordinator-messages-report",
    ),
]
