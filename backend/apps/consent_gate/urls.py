"""Adresy CONS-01 – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web``).

Ekran zgód stoi pod ``/me/…`` obok przełącznika publikacji nazwiska (``me/consents/publish-name/``):
to ta sama rodzina – zgody uczestnika. Jest w obszarze bramki i na jej liście dozwolonej
(``middleware.ALLOWED_VIEWS``). Prefiks ``/me/`` nie jest na liście page cache'u.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("me/consents/complete/", views.ConsentCompleteView.as_view(), name="consent-complete"),
    path(
        "coordinator/consents/missing.csv",
        views.ConsentGapExportView.as_view(),
        name="coordinator-consent-gaps",
    ),
]
