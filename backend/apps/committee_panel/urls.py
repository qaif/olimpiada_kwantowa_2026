"""Adresy ramy panelu komisji (UX-PANEL-03) – rozwijane w ``apps/web/urls.py`` (przestrzeń ``web``).

Prefiks ``committee/`` jest wspólny dla recenzenta i komisji odwoławczej: Start i Powiadomienia
obsługują obie role, a dotychczasowe kolejki (``/review/``, ``/appeals/``) zostają pod swoimi adresami.
Żadna nazwa nie pasuje do wzorca „treści zadań” bramki nadzoru (``apps/proctoring/tests/test_gate.py``):
materiały są ekranem komitetu, a wzorcówkę wydaje dalej ``web:problem-model-solution``.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("committee/", views.CommitteeStartView.as_view(), name="committee-start"),
    path("committee/materials/", views.CommitteeMaterialsView.as_view(), name="committee-materials"),
    path(
        "committee/notifications/",
        views.CommitteeNotificationListView.as_view(),
        name="committee-notifications",
    ),
    path(
        "committee/notifications/read-all/",
        views.CommitteeNotificationReadAllView.as_view(),
        name="committee-notifications-read-all",
    ),
    path(
        "committee/notifications/<int:pk>/",
        views.CommitteeNotificationOpenView.as_view(),
        name="committee-notification-open",
    ),
]
