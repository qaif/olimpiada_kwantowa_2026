"""Adresy API v2 dla serwisu na django CMS – montowane pod ``/internal/djcms/v2/`` (DJ-02 § 4).

Konkurs stoi **w ścieżce** (``c/<slug>/<endpoint>``), a nie w nagłówku: klucz bufora klienta,
dziennik dostępu i test widzą go wprost, a bramki DJ-01 zostają bez zmian (DJ-02 D11).

Slug ma kształt ``[a-z0-9-]{1,50}`` (``views.SLUG_PATTERN``); inny kształt (wielkie litery, kropka,
podkreślnik, za długi) nie dociera do widoku – kończy go catch-all tą samą pustą 404, co zamknięta
bramka. Każdy nieznany adres gałęzi (także prawdziwy z ukośnikiem na końcu) – to samo: bez 301
z ``APPEND_SLASH`` i bez strony 404 Wagtaila, które zdradzałyby, jakie nazwy istnieją.

Bez przestrzeni nazw i bez nazw wzorców – tych adresów nikt w aplikacji nie buduje przez
``reverse()``; woła je wyłącznie klient ``djcms`` z sieci compose'a.
"""

from __future__ import annotations

from django.urls import include, path, re_path

from . import views

competition_patterns = [
    path("chrome", views.chrome_v2),
    path("stages", views.stages_v2),
    path("problems", views.problems_v2),
    path("results", views.results_v2),
    path("editions", views.editions_v2),
    path("editions/<int:edition_id>/results", views.edition_results_v2),
    path("workshops", views.workshops_v2),
    path("partners", views.partners_v2),
    path("export", views.export_v2),
    re_path(r"^.*$", views.not_found),
]

urlpatterns = [
    path("competitions", views.competitions),
    re_path(rf"^c/(?P<slug>{views.SLUG_PATTERN})/", include(competition_patterns)),
    # Na końcu, zawsze – patrz docstring modułu.
    re_path(r"^.*$", views.not_found),
]
