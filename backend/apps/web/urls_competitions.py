"""Adresy ekranu „Nowy konkurs” – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł z tego samego powodu, co ``urls_consents`` i pozostałe listy wydań etapu 2
(``docs/UNIWERSALNY-ETAP-2.md`` § 4.1): ``apps/web/urls.py`` ma jednego właściciela na wydanie,
a montaż polega na rozwinięciu tej listy **na końcu** ``urlpatterns`` – kolejność wzorców jest
umową (etap 1 § 4.3), więc dopisujemy, a nie przestawiamy.

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tej instalacji i w tym
konkursie, rozstrzygają dwie bramki czytane w widoku (``PLATFORM_SUBDOMAINS`` i flaga
``competition_creation``; 404 przy którymkolwiek braku, § 2.1). Bramka w adresach znaczyłaby mapę
adresów zależną od konfiguracji, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` –
i pozycję menu, której nie da się sprawdzić testem widoku.

Nazwy są **dwie i różne co do liczby** (``coordinator-competitions`` – spis, ``coordinator-competition-new``
– formularz), więc żadna z nich nie koliduje z ``coordinator-competition`` (ekran „Ustawienia
konkursu”, etap 1). Ma to znaczenie dla podświetlenia pozycji menu: wzorzec dopasowania bez
myślnika na końcu porównuje się na **równość** (``apps/web/coordinator_nav.py``, ``Item``).
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_competitions

urlpatterns = [
    path(
        "coordinator/competitions/",
        coordinator_competitions.CompetitionListView.as_view(),
        name="coordinator-competitions",
    ),
    path(
        "coordinator/competitions/new/",
        coordinator_competitions.CompetitionCreateView.as_view(),
        name="coordinator-competition-new",
    ),
]
