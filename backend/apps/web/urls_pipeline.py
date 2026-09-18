"""Adresy edytora przebiegu i kategorii – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 jednego właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a trzy zadania montażowe wydania I biegną
równolegle. Montaż polega na rozwinięciu tej listy wewnątrz ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_pipeline import urlpatterns as pipeline_urlpatterns
    ...
    urlpatterns = [
        ...,
        *pipeline_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.

**Czynność ma własny adres, a nie pole „akcja”.** Przestawienie kroku, przesunięcie o jedno
miejsce, wyjęcie z toru i dopisanie reguły są czterema różnymi POST-ami pod cztery różne adresy –
ta sama reguła, co na ekranie integracji: rozróżnianie po nazwie wciśniętego przycisku zależy od
tego, czy przeglądarka ją przyśle, a przy wysyłce klawiaturą nie zawsze przysyła.

Adres komponentów wchodzi w gałąź ``coordinator/stages/<id>/``, mimo że widok stoi w module
edytora przebiegu. Adres opisuje **przedmiot** (jeden etap), a nie plik, w którym mieszka kod,
i tak samo jest zapisany w mapie ekranów (§ 2.2).
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_categories, coordinator_pipeline

urlpatterns = [
    # --- tor etapów --------------------------------------------------------------------------
    path(
        "coordinator/pipeline/",
        coordinator_pipeline.PipelineView.as_view(),
        name="coordinator-pipeline",
    ),
    path(
        "coordinator/pipeline/steps/",
        coordinator_pipeline.PipelineStepCreateView.as_view(),
        name="coordinator-pipeline-step-create",
    ),
    path(
        "coordinator/pipeline/rounds/",
        coordinator_pipeline.RoundStageCreateView.as_view(),
        name="coordinator-pipeline-round-create",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/",
        coordinator_pipeline.PipelineStepUpdateView.as_view(),
        name="coordinator-pipeline-step",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/move/<str:direction>/",
        coordinator_pipeline.PipelineStepMoveView.as_view(),
        name="coordinator-pipeline-step-move",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/delete/",
        coordinator_pipeline.PipelineStepDeleteView.as_view(),
        name="coordinator-pipeline-step-delete",
    ),
    # --- reguły przejścia kroku --------------------------------------------------------------
    path(
        "coordinator/pipeline/<int:step_id>/rules/",
        coordinator_pipeline.PipelineRulesView.as_view(),
        name="coordinator-pipeline-rules",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/rules/new/",
        coordinator_pipeline.TransitionRuleCreateView.as_view(),
        name="coordinator-pipeline-rule-create",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/rules/preview/",
        coordinator_pipeline.TransitionRulePreviewView.as_view(),
        name="coordinator-pipeline-rule-preview",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/rules/<int:rule_id>/",
        coordinator_pipeline.TransitionRuleEditView.as_view(),
        name="coordinator-pipeline-rule",
    ),
    path(
        "coordinator/pipeline/<int:step_id>/rules/<int:rule_id>/delete/",
        coordinator_pipeline.TransitionRuleDeleteView.as_view(),
        name="coordinator-pipeline-rule-delete",
    ),
    # --- komponenty etapu ---------------------------------------------------------------------
    path(
        "coordinator/stages/<int:stage_id>/components/",
        coordinator_pipeline.StageComponentsView.as_view(),
        name="coordinator-stage-components",
    ),
    path(
        "coordinator/stages/<int:stage_id>/components/new/",
        coordinator_pipeline.StageComponentCreateView.as_view(),
        name="coordinator-stage-component-create",
    ),
    path(
        "coordinator/stages/<int:stage_id>/components/<int:component_id>/",
        coordinator_pipeline.StageComponentEditView.as_view(),
        name="coordinator-stage-component",
    ),
    path(
        "coordinator/stages/<int:stage_id>/components/<int:component_id>/delete/",
        coordinator_pipeline.StageComponentDeleteView.as_view(),
        name="coordinator-stage-component-delete",
    ),
    # --- kategorie ----------------------------------------------------------------------------
    path(
        "coordinator/categories/",
        coordinator_categories.CategoryListView.as_view(),
        name="coordinator-categories",
    ),
    path(
        "coordinator/categories/new/",
        coordinator_categories.CategoryCreateView.as_view(),
        name="coordinator-category-create",
    ),
    path(
        "coordinator/categories/assign/",
        coordinator_categories.CategoryAssignView.as_view(),
        name="coordinator-categories-assign",
    ),
    path(
        "coordinator/categories/<int:pk>/",
        coordinator_categories.CategoryEditView.as_view(),
        name="coordinator-category",
    ),
    path(
        "coordinator/categories/<int:pk>/delete/",
        coordinator_categories.CategoryDeleteView.as_view(),
        name="coordinator-category-delete",
    ),
]
