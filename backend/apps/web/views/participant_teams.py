"""Karta „Moja drużyna” na pulpicie uczestnika (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3, § 2.3).

Osobny moduł z tego samego powodu, co ``participant_fees``: pulpit jest plikiem wspólnym kilku
zadań etapu 2 i ma z każdego nowego obszaru zobaczyć **jedno** wywołanie i jedno włączenie
szablonu. Karta istnieje wyłącznie w konkursie z flagą ``team_entries``, a Olimpiada Kwantowa jej
nie włącza – więc cały ten odczyt ma być wyłączalny w jednym miejscu i kosztować przy wyłączonej
fladze **zero zapytań** (funkcja wychodzi na pierwszym warunku).

**Karta jest tylko do czytania i to jest decyzja, a nie brak czasu.** Skład drużyny układa
organizator (``/coordinator/teams/``): to on odpowiada za to, kto z kim startuje, a zawodnik,
który sam wypisałby się z drużyny w przeddzień finału, unieważniłby zgłoszenie całego składu.

**Widać kody publiczne, nie nazwiska.** Kod jest pseudonimem, pod którym drużyna i jej zawodnicy
stoją w ogłaszanych tabelach; nazwiska kolegów z drużyny nie są tą informacją, po którą uczestnik
wchodzi na swój pulpit, a pulpit nie jest listą osób.
"""

from __future__ import annotations

from apps.competitions.services import teams_of

#: Ta sama nazwa, którą czyta bramka serwisu drużyn. Jedno wejście do flagi w tym obszarze.
TEAM_ENTRIES_FLAG = "team_entries"


def team_card_context(participant, competition) -> dict:
    """Kontekst karty „Moja drużyna” albo **pusty słownik** – i wtedy szablon nie ma czego włączyć.

    Pusto (bez ani jednego zapytania) w dwóch wypadkach: konkurs nie prowadzi zgłoszeń drużynowych
    albo nie wiadomo, czyj to pulpit. Uczestnik bez drużyny dostaje pustą listę i karty też nie
    zobaczy – „nie jesteś w żadnej drużynie” jest kafelkiem o niczym w konkursie, w którym część
    startuje pojedynczo.
    """
    if competition is None or not competition.has_feature(TEAM_ENTRIES_FLAG):
        return {}
    teams = list(teams_of(participant).select_related("edition").prefetch_related("members__participant"))
    return {"my_teams": teams} if teams else {}
