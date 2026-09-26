"""Adresy API dla wersji ``dj.`` – montowane pod ``/internal/djcms/v1/`` (``apps/tenancy/internal_urls.py``).

Bez przestrzeni nazw i bez nazw wzorców: tych adresów nikt w aplikacji nie buduje przez
``reverse()`` – woła je wyłącznie klient ``djcms`` z sieci compose'a.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("chrome", views.chrome),
    path("stages", views.stages),
    path("problems", views.problems),
    path("results", views.results),
    path("editions", views.editions),
    path("editions/<int:edition_id>/results", views.edition_results),
    path("workshops", views.workshops),
    path("export", views.export),
]
