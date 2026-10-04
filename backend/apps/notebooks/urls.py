"""Adresy notatników kwantowych – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web``).

Osobny moduł z tego samego powodu co ``apps/web/urls_ai_grading.py``: ``urls.py`` zmienia kilka
równoległych zadań, a jedna linijka rozwinięcia listy jest jedynym miejscem wspólnym. Bramki
flagi tu nie ma – rozstrzyga widok (404), żeby ``reverse()`` działał zawsze tak samo.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("coordinator/notebooks/", views.NotebookListView.as_view(), name="coordinator-notebooks"),
    path(
        "coordinator/notebooks/problems/<int:pk>/",
        views.NotebookTaskView.as_view(),
        name="coordinator-notebook-task",
    ),
    path(
        "coordinator/notebooks/problems/<int:pk>/starter.ipynb",
        views.NotebookStarterPreviewView.as_view(),
        name="coordinator-notebook-starter",
    ),
    path(
        "coordinator/notebooks/problems/<int:pk>/results/",
        views.NotebookResultsView.as_view(),
        name="coordinator-notebook-results",
    ),
    path(
        "coordinator/notebooks/runs/<int:pk>/",
        views.NotebookRunDetailView.as_view(),
        name="coordinator-notebook-run",
    ),
    path("me/notebooks/<int:pk>/", views.ParticipantLabView.as_view(), name="participant-notebook"),
    # Bez prefiksu konkursu i z tokenem – patrz ``ParticipantStarterView`` i polityka laboratorium.
    path(
        "notebook-starter/<str:token>/<str:filename>",
        views.ParticipantStarterView.as_view(),
        name="participant-notebook-starter",
    ),
]
