"""Adresy edytora LaTeX – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web``).

Jak ``apps/notebooks/urls.py``: bramki flagi tu nie ma – rozstrzyga widok (404), żeby ``reverse()``
działał zawsze tak samo. Oba adresy uczestnika stoją za bramką nadzoru zdalnego
(``apps.proctoring.middleware.GATED_VIEWS``).
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path(
        "me/stages/<int:stage_id>/problems/<int:number>/latex/",
        views.ProblemLatexOpenView.as_view(),
        name="problem-latex",
    ),
    path(
        "me/stages/<int:stage_id>/problems/<int:number>/latex/submit/",
        views.ProblemLatexSubmitView.as_view(),
        name="problem-latex-submit",
    ),
    path(
        "coordinator/stages/<int:stage_id>/latex/",
        views.StageLatexSettingsView.as_view(),
        name="coordinator-stage-latex",
    ),
]
