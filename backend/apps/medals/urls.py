"""Adresy medali (MED-01). ``apps/web/urls.py`` rozwija tę listę na końcu ``urlpatterns`` (przestrzeń
nazw ``web``), tak jak delegacje – bramka flagi konkursu jest w widokach (404), żeby ``reverse()``
dawał ten sam adres w każdym konkursie.

Strony publiczne stoją pod istniejącym pierwszym segmentem ``results/`` – kontrakt tras dla
djcms (``djcms_contract/app_routes.json``) zostaje bez zmian.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    # --- koordynator ---------------------------------------------------------------------------
    path("coordinator/medals/", views.MedalStageListView.as_view(), name="coordinator-medals"),
    path("coordinator/medals/<int:stage_id>/", views.MedalSchemeView.as_view(), name="coordinator-medal"),
    path(
        "coordinator/medals/<int:stage_id>/overrides/",
        views.OverrideSetView.as_view(),
        name="coordinator-medal-override",
    ),
    path(
        "coordinator/medals/<int:stage_id>/overrides/<int:entry_id>/remove/",
        views.OverrideRemoveView.as_view(),
        name="coordinator-medal-override-remove",
    ),
    path(
        "coordinator/medals/<int:stage_id>/freeze/",
        views.FreezeView.as_view(),
        name="coordinator-medal-freeze",
    ),
    path(
        "coordinator/medals/<int:stage_id>/unfreeze/",
        views.UnfreezeView.as_view(),
        name="coordinator-medal-unfreeze",
    ),
    path(
        "coordinator/medals/<int:stage_id>/certificates/",
        views.IssueCertificatesView.as_view(),
        name="coordinator-medal-certificates",
    ),
    path(
        "coordinator/medals/<int:stage_id>/certificates.zip",
        views.CertificatesZipView.as_view(),
        name="coordinator-medal-certificates-zip",
    ),
    path(
        "coordinator/medals/<int:stage_id>/export.csv",
        views.ExportCsvView.as_view(),
        name="coordinator-medal-export-csv",
    ),
    path(
        "coordinator/medals/<int:stage_id>/ceremony.pdf",
        views.ExportPdfView.as_view(),
        name="coordinator-medal-export-pdf",
    ),
    # --- publiczne -----------------------------------------------------------------------------
    path("results/<int:stage_id>/medals/", views.PublicMedalsView.as_view(), name="results-medals"),
    path("results/<int:stage_id>/countries/", views.PublicCountriesView.as_view(), name="results-countries"),
]
