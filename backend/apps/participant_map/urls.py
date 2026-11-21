"""Adresy MAP-01 – doklejane na końcu ``apps/web/urls.py`` (przestrzeń nazw ``web:``).

``dokumenty/mapa-uczestnikow/`` stoi w urlconfie aplikacji, czyli przed drzewem stron Wagtaila:
sekcja „Dokumenty” zostaje treścią CMS-u, a ta podstrona należy do aplikacji (manifest tras djcms:
``dokumenty/mapa-uczestnikow`` w ``nested_paths``, ``apps.core.app_routes``). Strona leży pod
prefiksem ``/dokumenty/`` pełnostronicowego cache'u gościa (``apps.web.page_cache``).
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("dokumenty/mapa-uczestnikow/", views.ParticipantMapView.as_view(), name="participant-map"),
    path(
        "coordinator/participant-map/",
        views.CoordinatorMapView.as_view(),
        name="coordinator-participant-map",
    ),
    path(
        "coordinator/participant-map/refresh/",
        views.CoordinatorMapRefreshView.as_view(),
        name="coordinator-participant-map-refresh",
    ),
]
