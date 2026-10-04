"""Adresy nadzoru zdalnego (zadanie PROC-01, ``apps.web.views.proctoring``).

Uczeń – pod ``/me/proctoring/…`` (jego panel), koordynator – pod ``/coordinator/proctoring/…``,
nadzorujący – pod **nowym** pierwszym segmentem ``proctoring`` (wspólnym dla koordynatorów, komisji
i opiekunów drużyn, więc ani ``me``, ani ``review``). Segment wszedł do kontraktu tras djcms
(``manage.py djcms_routes --write``), do ``RESERVED_SLUGS`` i do ``PRIVATE_PREFIXES``.

Wzorce stoją w mapie zawsze; bez flagi konkursu ``proctoring`` widoki odpowiadają 404.
"""

from __future__ import annotations

from django.urls import path

from .views import proctoring

urlpatterns = [
    path(
        "me/proctoring/<int:stage_id>/", proctoring.ProctoringConsoleView.as_view(), name="proctoring-console"
    ),
    path(
        "me/proctoring/<int:stage_id>/consent/",
        proctoring.ProctoringConsentView.as_view(),
        name="proctoring-consent",
    ),
    path(
        "me/proctoring/<int:stage_id>/alternative/",
        proctoring.ProctoringAlternativeView.as_view(),
        name="proctoring-alternative",
    ),
    path(
        "me/proctoring/<int:stage_id>/token/",
        proctoring.ProctoringStudentTokenView.as_view(),
        name="proctoring-student-token",
    ),
    path(
        "me/proctoring/<int:stage_id>/messages/",
        proctoring.ProctoringStudentMessagesView.as_view(),
        name="proctoring-student-messages",
    ),
    path(
        "me/proctoring/<int:stage_id>/do/<slug:action>/",
        proctoring.ProctoringStudentApiView.as_view(),
        name="proctoring-student-action",
    ),
    path("proctoring/", proctoring.ProctorStagesView.as_view(), name="proctoring"),
    path("proctoring/<int:stage_id>/", proctoring.ProctorGridView.as_view(), name="proctoring-grid"),
    path(
        "proctoring/<int:stage_id>/roster/", proctoring.ProctorRosterView.as_view(), name="proctoring-roster"
    ),
    path("proctoring/<int:stage_id>/token/", proctoring.ProctorTokenView.as_view(), name="proctoring-token"),
    path(
        "proctoring/<int:stage_id>/s/<int:pk>/action/",
        proctoring.ProctorActionView.as_view(),
        name="proctoring-action",
    ),
    path(
        "proctoring/<int:stage_id>/s/<int:pk>/",
        proctoring.ProctoringReportView.as_view(),
        name="proctoring-report",
    ),
    path(
        "proctoring/<int:stage_id>/export.csv",
        proctoring.ProctoringExportView.as_view(),
        name="proctoring-export",
    ),
    path(
        "proctoring/<int:stage_id>/media/<slug:kind>/<int:pk>/",
        proctoring.ProctoringMediaView.as_view(),
        name="proctoring-media",
    ),
    path(
        "coordinator/proctoring/",
        proctoring.CoordinatorProctoringView.as_view(),
        name="coordinator-proctoring",
    ),
    path(
        "coordinator/proctoring/<int:stage_id>/",
        proctoring.CoordinatorProctoringStageView.as_view(),
        name="coordinator-proctoring-stage",
    ),
]
