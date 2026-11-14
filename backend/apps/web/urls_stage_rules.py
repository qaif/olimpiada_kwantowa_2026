"""Adresy „Zasad Organizacji Zawodów” etapu (ZOZ-01) – strona publiczna i ekrany koordynatora.

Osobny moduł, rozwijany na **końcu** ``urlpatterns`` w ``apps/web/urls.py`` – kolejność wzorców jest
umową, więc dopisujemy, a nie wstawiamy w środek.

``/dokumenty/zasady/<etap>/…`` stoi w urlconfie aplikacji, czyli **przed** drzewem stron Wagtaila:
sekcja „Dokumenty” zostaje treścią CMS-u, a jej podstrona ``zasady/<etap>/`` należy do aplikacji
(manifest tras djcms: ``dokumenty/zasady`` w ``nested_paths``, ``apps.core.app_routes``). Sam adres
``/dokumenty/zasady/`` (bez etapu) nie ma tu wzorca. Segment ``zasady``, a nie ``zoz``: pod
``/dokumenty/zoz/`` leży (ukryta) strona Wagtaila z projektem ZOZ edycji 0.2 z ``seed_legacy_content``,
a strona z seedów nie może stać pod adresem aplikacji.
Strona publiczna stoi pod prefiksem ``/dokumenty/`` pełnostronicowego cache'u
(``apps.web.page_cache``); każdy zapis wersji unieważnia cache konkursu.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_stage_rules as coordinator
from .views import stage_rules as public

urlpatterns = [
    # --- strona publiczna -----------------------------------------------------------------------
    path("dokumenty/zasady/<int:stage_id>/", public.StageRulesView.as_view(), name="stage-rules"),
    path(
        "dokumenty/zasady/<int:stage_id>/wersja/<str:version>/",
        public.StageRulesView.as_view(),
        name="stage-rules-version",
    ),
    path(
        "dokumenty/zasady/<int:stage_id>/wersja/<str:version>/pdf/",
        public.StageRulesPdfView.as_view(),
        name="stage-rules-pdf",
    ),
    # --- panel koordynatora ---------------------------------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/rules/",
        coordinator.StageRulesListView.as_view(),
        name="coordinator-stage-rules",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/",
        coordinator.StageRulesEditView.as_view(),
        name="coordinator-stage-rules-edit",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/preview/",
        coordinator.StageRulesPreviewView.as_view(),
        name="coordinator-stage-rules-preview",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/pdf/",
        coordinator.StageRulesPdfView.as_view(),
        name="coordinator-stage-rules-pdf",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/publish/",
        coordinator.StageRulesPublishView.as_view(),
        name="coordinator-stage-rules-publish",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/withdraw/",
        coordinator.StageRulesWithdrawView.as_view(),
        name="coordinator-stage-rules-withdraw",
    ),
    path(
        "coordinator/stage-rules/<int:pk>/delete/",
        coordinator.StageRulesDeleteView.as_view(),
        name="coordinator-stage-rules-delete",
    ),
]
