"""Karta uczestnika w panelu koordynatora – jedna strona o jednej osobie.

Osobny moduł od ``coordinator_accounts.py``, choć obie strony dotyczą tego samego człowieka:
tamta jest **formularzem konta** (imię, adres, blokada logowania, usunięcie) i należy do
administracji kontami, ta jest **aktami sprawy** i należy do prowadzenia zawodów. Sklejenie ich
w jeden ekran znaczyłoby, że koordynator szukający odpowiedzi na „czy praca doszła” przewija
formularz, w którym łatwo przypadkiem coś zapisać.

Widok jest wyłącznie odczytem: cały stan składa ``apps.accounts.participant_card``, a każda
czynność na stronie celuje w **istniejący** widok-akcję koordynatora (przydział recenzenta,
korekta punktów, ocena końcowa, blokada do oceny, aktywacja konta, eksport danych, usunięcie).
Dzięki temu reguła domenowa ma nadal jedno miejsce, a karta nie dokłada ani jednej nowej drogi
zapisu, którą trzeba by osobno pilnować.

Dokąd wraca akcja po zapisie: tam, gdzie wraca zawsze (ekran przydziałów etapu albo pulpit) –
``ActionViewMixin`` nie czyta ``?next=`` ani nagłówka ``Referer``. Karta tego nie obchodzi
własnym przekierowaniem, bo to znaczyłoby drugą regułę powrotu obok istniejącej; gdy baza akcji
dostanie powrót po adresie odsyłającym, karta skorzysta z niej bez żadnej zmiany tutaj.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.views.generic import View

from apps.accounts.participant_card import card_queryset, participant_card
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/participant_detail.html"


class CoordinatorParticipantView(CoordinatorRequiredMixin, View):
    """``/coordinator/participants/<pk>/`` – karta jednego uczestnika (klucz profilu, nie konta).

    Klucz jest kluczem ``Participant``, a nie ``User``, i to jest świadome: karta opisuje udział
    w zawodach, a ten wisi na profilu uczestnika. Konto bez profilu (recenzent, opiekun, konto
    porzucone w połowie rejestracji) nie ma karty i dostaje 404 – jego miejscem jest lista kont.
    """

    def get(self, request, pk: int):
        participant = get_object_or_404(card_queryset().for_competition(request.competition), pk=pk)
        return TemplateResponse(request, TEMPLATE, participant_card(participant))
