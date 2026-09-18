"""Adresy ekranu „Zgody konkursu” – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 jednego właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a ekran powstaje równolegle z innymi. Montaż
polega na rozwinięciu tej listy wewnątrz ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_consents import urlpatterns as consent_urlpatterns
    ...
    urlpatterns = [
        ...,
        *consent_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_consents

urlpatterns = [
    path(
        "coordinator/consents/",
        coordinator_consents.ConsentListView.as_view(),
        name="coordinator-consents",
    ),
    path(
        "coordinator/consents/<int:pk>/",
        coordinator_consents.ConsentEditView.as_view(),
        name="coordinator-consent-edit",
    ),
    path(
        "coordinator/consents/<int:pk>/version/",
        coordinator_consents.ConsentVersionView.as_view(),
        name="coordinator-consent-version",
    ),
]
