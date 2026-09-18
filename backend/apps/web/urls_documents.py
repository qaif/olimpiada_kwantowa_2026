"""Adresy ekranu „Szablony dokumentów” – wzorce gotowe do wpięcia w ``apps/web/urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 jednego właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a ekran powstaje równolegle z innymi. Montaż
polega na rozwinięciu tej listy wewnątrz ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_documents import urlpatterns as document_urlpatterns
    ...
    urlpatterns = [
        ...,
        *document_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.

Rodzaj dokumentu jedzie w adresie **swoją wartością** (``LAUREAT``, ``GUARDIAN_FORM``), a nie
identyfikatorem wiersza. Powód jest jeden i praktyczny: rodzaj istnieje także wtedy, gdy konkurs
nie ma jeszcze ani jednego szablonu tego rodzaju, więc adres „po wierszu” nie miałby dokąd
prowadzić przed pierwszą publikacją. Lista rodzajów jest zamknięta w kodzie, a wartość spoza niej
daje 404 w widoku – konwerter ``str`` przyjmuje więc wszystko, co nie zawiera ukośnika, i to
widok rozstrzyga, czy to jest rodzaj.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_documents

urlpatterns = [
    path(
        "coordinator/documents/",
        coordinator_documents.DocumentTemplateListView.as_view(),
        name="coordinator-documents",
    ),
    path(
        "coordinator/documents/<str:kind>/",
        coordinator_documents.DocumentTemplateDetailView.as_view(),
        name="coordinator-document",
    ),
]
