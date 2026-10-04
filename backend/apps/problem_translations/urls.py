"""Adresy tłumaczeń zadań (TR-01) – rozwijane na końcu ``apps/web/urls.py`` (przestrzeń nazw ``web``).

Bramki trybu konkursu tu nie ma – rozstrzyga ją widok (404 poza trybem ``DELEGATIONS``), tak jak
w ``urls_delegations``. Stałe ścieżki (``source.pdf``, ``languages/``) stoją przed wzorcami z parametrem.
"""

from __future__ import annotations

from django.urls import path

from . import views_coordinator as coordinator
from . import views_leader as leader
from . import views_student as student

urlpatterns = [
    # --- opiekun drużyny ---------------------------------------------------------------------
    path(
        "delegation/translations/", leader.TranslationsDashboardView.as_view(), name="delegation-translations"
    ),
    path(
        "delegation/translations/languages/",
        leader.LanguagesView.as_view(),
        name="delegation-translations-languages",
    ),
    path(
        "delegation/translations/students/<int:pk>/language/",
        leader.StudentLanguageView.as_view(),
        name="delegation-translations-student-language",
    ),
    path(
        "delegation/translations/problems/<int:pk>/source.pdf",
        leader.SourcePdfView.as_view(),
        name="delegation-translation-source-pdf",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/",
        leader.EditorView.as_view(),
        name="delegation-translation",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/autosave/",
        leader.AutosaveView.as_view(),
        name="delegation-translation-autosave",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/upload/",
        leader.UploadView.as_view(),
        name="delegation-translation-upload",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/submit/",
        leader.SubmitView.as_view(),
        name="delegation-translation-submit",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/withdraw/",
        leader.WithdrawView.as_view(),
        name="delegation-translation-withdraw",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/reopen/",
        leader.ReopenView.as_view(),
        name="delegation-translation-reopen",
    ),
    path(
        "delegation/translations/problems/<int:pk>/<str:language>/file.pdf",
        leader.TranslationPdfView.as_view(),
        name="delegation-translation-file",
    ),
    # --- koordynator / komisja ---------------------------------------------------------------
    path("coordinator/translations/", coordinator.OverviewView.as_view(), name="coordinator-translations"),
    path(
        "coordinator/translations/stages/<int:pk>/",
        coordinator.StageView.as_view(),
        name="coordinator-translations-stage",
    ),
    path(
        "coordinator/translations/stages/<int:pk>/export/<str:language>/",
        coordinator.ExportPrintView.as_view(),
        name="coordinator-translations-print",
    ),
    path(
        "coordinator/translations/stages/<int:pk>/export/<str:language>/pdf/",
        coordinator.ExportPdfView.as_view(),
        name="coordinator-translations-pdf",
    ),
    path(
        "coordinator/translations/problems/<int:pk>/source/",
        coordinator.SourceView.as_view(),
        name="coordinator-translations-source",
    ),
    path(
        "coordinator/translations/revisions/<int:pk>/file.pdf",
        coordinator.RevisionFileView.as_view(),
        name="coordinator-translation-revision-file",
    ),
    path(
        "coordinator/translations/<int:pk>/", coordinator.ReviewView.as_view(), name="coordinator-translation"
    ),
    path(
        "coordinator/translations/<int:pk>/approve/",
        coordinator.ApproveView.as_view(),
        name="coordinator-translation-approve",
    ),
    path(
        "coordinator/translations/<int:pk>/return/",
        coordinator.ReturnView.as_view(),
        name="coordinator-translation-return",
    ),
    # --- uczeń --------------------------------------------------------------------------------
    path(
        "me/translations/problems/<int:pk>/", student.StudentProblemView.as_view(), name="student-translation"
    ),
    path(
        "me/translations/problems/<int:pk>/file.pdf",
        student.StudentProblemPdfView.as_view(),
        name="student-translation-file",
    ),
]
