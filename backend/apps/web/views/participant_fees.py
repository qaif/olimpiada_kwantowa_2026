"""Kafel „Wpisowe” na pulpicie uczestnika i pobranie wydanego rachunku.

Osobny moduł, a nie kilka metod w ``apps.web.views.participant`` – z dwóch powodów. Pierwszy jest
organizacyjny: pulpit jest plikiem wspólnym dla kilku zadań etapu 2, a obszar finansów ma tam
wejść **jednym** wywołaniem i jednym włączeniem szablonu. Drugi jest merytoryczny: kafel istnieje
wyłącznie w konkursie z flagą ``fees``, a Olimpiada Kwantowa jej nie włącza – więc cały ten kod ma
być wyłączalny w jednym miejscu i kosztować przy wyłączonej fladze **zero zapytań**
(:func:`fee_card_context` wychodzi na pierwszym warunku).

**Uczestnik widzi wyłącznie swoją należność i wyłącznie to, co jest mu potrzebne**: kwotę, stan,
termin, oznaczenie do przelewu i – jeżeli organizator go wydał – rachunek do pobrania. Nie widzi
cennika, powodu zwolnienia wpisanego przez koordynatora ani czyjejkolwiek cudzej należności:
``fee_for`` pyta po **swoim** profilu uczestnika, a profil jest profilem tego konkursu.

**Rachunku nie przechowujemy**, tak samo jak dyplomu: powstaje przy każdym pobraniu z wersji
szablonu **zapamiętanej w chwili wydania** (``ParticipantFee.document_version``). Dlatego pobranie
za rok daje ten sam papier, co dziś, nawet jeżeli organizator zdążył poprawić tekst. Dopóki
koordynator dokumentu nie wydał, adres oddaje 404 – uczestnik nie wydaje sobie rachunku sam,
bo wydanie jest decyzją organizatora i zostawia ślad w rejestrze.
"""

from __future__ import annotations

from django.http import Http404
from django.views.generic import View

from apps.competitions.services import current_edition
from apps.tenancy.documents import render_document
from apps.tenancy.fees import DOCUMENT_KIND, FeeStatus, fee_document_context, fee_for, fees_enabled
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.views.coordinator_fees import fee_document_response


def fee_card_context(competition, participant, edition) -> dict:
    """Kontekst kafla „Wpisowe” albo **pusty słownik** – i wtedy szablon nie ma czego włączyć.

    Pusto (bez ani jednego zapytania) w trzech wypadkach: konkurs nie pobiera wpisowego, nie ma
    edycji bieżącej, tej osobie nic nie naliczono. Trzeci jest zwykłym stanem, a nie brakiem:
    wpisowe bywa naliczane dopiero po zakwalifikowaniu do etapu, a kafel „nie masz nic do
    zapłacenia” byłby kafelkiem o niczym.

    Konkurs przychodzi **argumentem**, a nie przez ``participant.competition``: ta druga droga
    jest kluczem obcym, czyli zapytaniem na każdym wejściu do panelu – także w konkursie, który
    wpisowego nie pobiera. Wołający ma konkurs w ręku (``request.competition``), więc niech go
    poda; to jest ta sama reguła, co przy ``current_edition`` (§ 1.0 (b)).
    """
    if edition is None or not fees_enabled(competition):
        return {}
    fee = fee_for(participant, edition)
    if fee is None:
        return {}
    return {
        "fee": fee,
        "fee_status_label": dict(FeeStatus.choices)[fee.status],
        "fee_settled": fee.is_settled,
        # Oznaczenie do wpisania w tytule przelewu. **Nie jest numerem faktury** (D15) – to jest
        # klucz do wiersza rejestru, po którym organizator odnajdzie wpłatę.
        "fee_reference": fee.reference,
        "fee_document_ready": bool(fee.document_version),
    }


class FeeDocumentView(ParticipantRequiredMixin, View):
    """``GET /me/fees/document/`` – rachunek wydany przez organizatora, w swojej wersji.

    404 zamiast 403 w każdym z trzech wypadków (konkurs bez wpisowego, brak należności, dokument
    jeszcze niewydany): dla uczestnika to jest ten sam stan – „nie ma tu nic do pobrania” – a trzy
    różne kody odpowiedzi opowiadałyby mu o konfiguracji konkursu.
    """

    def get(self, request):
        edition = current_edition(self.competition)
        if edition is None or not fees_enabled(self.competition):
            raise Http404("Ten konkurs nie pobiera wpisowego.")
        fee = fee_for(self.participant, edition)
        if fee is None or not fee.document_version:
            raise Http404("Nie wydano jeszcze rachunku za to wpisowe.")
        rendered = render_document(
            self.competition,
            DOCUMENT_KIND,
            version=fee.document_version,
            **fee_document_context(fee),
        )
        return fee_document_response(rendered, fee)
