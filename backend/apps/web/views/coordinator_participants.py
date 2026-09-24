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
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/participant_detail.html"


class CoordinatorParticipantView(CoordinatorRequiredMixin, View):
    """``/coordinator/participants/<pk>/`` – karta jednego uczestnika (klucz profilu, nie konta).

    Klucz jest kluczem ``Participant``, a nie ``User``, i to jest świadome: karta opisuje udział
    w zawodach, a ten wisi na profilu uczestnika. Konto bez profilu (recenzent, opiekun, konto
    porzucone w połowie rejestracji) nie ma karty i dostaje 404 – jego miejscem jest lista kont.
    """

    def get(self, request, pk: int):
        queryset = card_queryset().for_competition(request.competition)
        # Podział terytorialny konkursu (§ 1.4). Przy wyłączonej fladze – czyli w Konkursie #1 –
        # karta nie dotyka kolumny ``region`` ani razu: ani ``select_related``, ani odczytu, ani
        # zmiennej w kontekście. Przy włączonej nazwa regionu zastępuje etykietę województwa,
        # a ``select_related`` trzyma obietnicę „stała liczba zapytań na kartę”.
        by_region = self._regions_enabled(request)
        if by_region:
            queryset = queryset.select_related("region")
        participant = get_object_or_404(queryset, pk=pk)
        context = participant_card(participant)
        context["region_label"] = participant.region.name if by_region and participant.region_id else ""
        context.update(self._student_status(request, participant))
        return TemplateResponse(request, TEMPLATE, context)

    @staticmethod
    def _student_status(request, participant) -> dict:
        """Stan zaświadczenia o statusie ucznia w edycji bieżącej – albo nic, bez ani jednego zapytania.

        Tylko w konkursie z flagą ``student_status_certificate`` (czyta ją widok, nie szablon –
        § 2.1). Karta pokazuje stan i historię wersji; decyzje zapadają na ekranie
        ``/coordinator/student-status/``, do którego karta prowadzi odnośnikiem – jedno miejsce
        decyzji, tak jak przy pozostałych czynnościach karty.
        """
        from apps.competitions.services import current_edition
        from apps.student_status import services as student_status
        from apps.student_status.models import enabled

        competition = getattr(request, "competition", None)
        if not enabled(competition):
            return {}
        edition = current_edition(competition)
        if edition is None:
            return {}
        return {
            "student_status": student_status.status_summary(participant, edition),
            "student_status_history": student_status.history(participant, edition),
        }

    @staticmethod
    def _regions_enabled(request) -> bool:
        """Czy ten konkurs ma własny podział terytorialny. Flagę czyta widok, nigdy szablon (§ 2.1)."""
        competition = getattr(request, "competition", None)
        return competition is not None and competition.has_feature(CUSTOM_REGIONS_FLAG)
