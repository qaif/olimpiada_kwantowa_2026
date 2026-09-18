"""Adresy ekranu „Regiony” – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 jednego właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a zadania wydania G biegną równolegle. Montaż
polega na rozwinięciu tej listy wewnątrz ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_regions import urlpatterns as region_urlpatterns
    ...
    urlpatterns = [
        ...,
        *region_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.

**Czynność ma własny adres, a nie pole „akcja”.** Dopisanie regionu, przesunięcie o jedno miejsce,
wyłączenie, włączenie, usunięcie i uzupełnienie zestawu startowego są sześcioma różnymi POST-ami
pod sześć różnych adresów – ta sama reguła, co na ekranie integracji i w edytorze przebiegu:
rozróżnianie po nazwie wciśniętego przycisku zależy od tego, czy przeglądarka ją przyśle, a przy
wysyłce klawiaturą nie zawsze przysyła.

Adres ``defaults/`` stoi **przed** ``<int:pk>/`` i to nie jest kwestia gustu: wzorzec liczbowy nie
złapałby napisu, ale kolejność wzorców jest umową, a czytelniejsza jest ta, w której stała ścieżka
poprzedza wzorzec z parametrem.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_regions

urlpatterns = [
    path(
        "coordinator/regions/",
        coordinator_regions.RegionListView.as_view(),
        name="coordinator-regions",
    ),
    path(
        "coordinator/regions/new/",
        coordinator_regions.RegionCreateView.as_view(),
        name="coordinator-region-create",
    ),
    path(
        "coordinator/regions/defaults/",
        coordinator_regions.RegionDefaultsView.as_view(),
        name="coordinator-regions-defaults",
    ),
    path(
        "coordinator/regions/<int:pk>/",
        coordinator_regions.RegionEditView.as_view(),
        name="coordinator-region",
    ),
    path(
        "coordinator/regions/<int:pk>/move/<str:direction>/",
        coordinator_regions.RegionMoveView.as_view(),
        name="coordinator-region-move",
    ),
    path(
        "coordinator/regions/<int:pk>/deactivate/",
        coordinator_regions.RegionDeactivateView.as_view(),
        name="coordinator-region-deactivate",
    ),
    path(
        "coordinator/regions/<int:pk>/activate/",
        coordinator_regions.RegionActivateView.as_view(),
        name="coordinator-region-activate",
    ),
    path(
        "coordinator/regions/<int:pk>/delete/",
        coordinator_regions.RegionDeleteView.as_view(),
        name="coordinator-region-delete",
    ),
]
