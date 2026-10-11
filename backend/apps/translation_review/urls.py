"""Adresy przeglądu tłumaczeń (L10N-01). Rozwijane na **końcu** ``apps.web.urls`` (przestrzeń ``web``).

``report/`` stoi przed ``<language>/``, bo inaczej słowo „report” dopasowałoby się jako kod języka
(i skończyło 404 z ``LanguageMixin``). Klucz napisu (SHA-256) szukany jest w indeksie katalogów,
a nie w bazie – klucz, którego tam nie ma, kończy się 404 bez zapytania.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("translations/", views.HomeView.as_view(), name="translations"),
    path("translations/report/", views.ReportView.as_view(), name="translation-report"),
    path("translations/<str:language>/", views.StringListView.as_view(), name="translation-list"),
    path("translations/<str:language>/reports/", views.ReportsView.as_view(), name="translation-reports"),
    path(
        "translations/<str:language>/<slug:key>/", views.StringDetailView.as_view(), name="translation-string"
    ),
    path(
        "coordinator/translators/",
        views.CoordinatorTranslatorsView.as_view(),
        name="coordinator-translators",
    ),
]
