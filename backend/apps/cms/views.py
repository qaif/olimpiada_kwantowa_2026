"""Serwowanie dokumentów Wagtaila (``/documents/<id>/<nazwa>``) z nagłówkiem cache.

Dokumenty idą przez widok aplikacji, a nie przez adres obiektu w buckecie
(``WAGTAILDOCS_SERVE_METHOD = "serve_view"`` – uzasadnienie w ``config/settings/base.py``).
Widok Wagtaila nie ustawia ``Cache-Control``, więc każde wejście na ``/regulamin/`` z otwartym
PDF-em przepycha przez aplikację cały plik jeszcze raz: 13-stronicowy regulamin to ćwierć
megabajta na każde odświeżenie, a plik zmienia się raz na edycję.

Godzina jest kompromisem między tym a poprawką wgraną w ``/cms/``: redaktor podmieniający
załącznik zobaczy nową treść u siebie od razu (Wagtail wgrywa plik pod nową nazwą, więc adres
się zmienia), a czytelnik z buforem przeglądarki – najpóźniej po godzinie.

Nagłówek dostają wyłącznie dokumenty z kolekcji **bez ograniczeń widoczności**. Kolekcja
zamknięta („tylko zalogowani”, hasło) oznacza, że treść zależy od tego, kto pyta – ``public``
w pamięci proxy pozwoliłby ją wtedy oddać komuś innemu.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from wagtail.documents import get_document_model
from wagtail.documents.views.serve import serve as wagtail_serve

#: Godzina w buforze przeglądarki i pośredników. ``public``, bo plik jest ten sam dla każdego.
PUBLIC_CACHE_CONTROL = "public, max-age=3600"

#: Kody, dla których nagłówek ma sens: pełna odpowiedź i „nic się nie zmieniło” z ETagu.
CACHEABLE_STATUSES = frozenset({200, 304})


def is_public_document(document) -> bool:
    """Czy dokument leży w kolekcji bez ograniczeń widoczności (ta sama reguła, co w hooku Wagtaila)."""
    return not document.collection.get_view_restrictions().exists()


def serve(request, document_id, document_filename):
    """``wagtail.documents.views.serve.serve`` plus ``Cache-Control`` dla plików publicznych.

    Dokument czytamy tu drugi raz (widok Wagtaila robi to u siebie) – jedno dodatkowe zapytanie
    po kluczu głównym. Alternatywa, czyli przepisanie widoku Wagtaila, oznaczałaby utrzymywanie
    kopii jego obsługi ``sendfile``, hooków i sygnałów.
    """
    response = wagtail_serve(request, document_id, document_filename)
    if response.status_code not in CACHEABLE_STATUSES:
        return response
    document = get_object_or_404(get_document_model(), id=document_id)
    if is_public_document(document):
        response["Cache-Control"] = PUBLIC_CACHE_CONTROL
    return response
