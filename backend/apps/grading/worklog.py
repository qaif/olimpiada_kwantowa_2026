"""Czas pracy nad recenzją: pomiar, sumowanie i zamiana sekund na zdanie po polsku.

Po co (prośba organizatora): planowanie obciążenia komitetu opiera się dziś na wyczuciu („zadanie 3
idzie wolno, dajmy mniej prac”). Licznik zamienia to wyczucie w liczbę – przy recenzji i przy
recenzencie na ekranie postępu etapu.

**Czego ten moduł nie robi i robić nie będzie**: nie zapisuje, co recenzent pisał, gdzie klikał ani
kiedy dokładnie przerywał. Do bazy trafiają trzy znaczniki czasu i jedna suma sekund. To wystarczy
na pytanie „ile godzin zajmuje ocena jednego zadania” i jest za mało na ocenę człowieka. Zasada
jest powtórzona w README (sekcja „Czas pracy nad recenzją”), bo obietnica złożona tylko w kodzie
nie jest obietnicą złożoną komitetowi.

Jak liczy się czas. Przeglądarka wysyła sygnał życia co ``HEARTBEAT_SECONDS`` i przestaje go
wysyłać po ``IDLE_SECONDS`` bez ruchu myszy i klawiatury. Serwer **nie ufa** klientowi co do
długości: dolicza ``min(czas od ostatniego sygnału, MAX_HEARTBEAT_GAP)``. Sufit jest sednem
pomiaru – bez niego karta zostawiona otwarta na noc dopisałaby recenzentowi osiem godzin pracy,
a przeglądarka uśpiona na dziesięć minut (przełączona karta zwalnia timery) zgubiłaby wszystko.
Sufit jest odrobinę wyższy niż odstęp sygnałów, żeby zwolniony timer w tle nie obcinał pomiaru
przy każdym przełączeniu karty.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Review, ReviewWorkLog

#: Co ile sekund przeglądarka wysyła sygnał życia. Wartość jest w JS-ie i tutaj – tutaj, bo to ona
#: wyznacza sufit doliczenia, a nie odwrotnie.
HEARTBEAT_SECONDS = 60

#: Po ilu sekundach bez ruchu myszy i klawiatury przeglądarka przestaje wysyłać sygnały.
#: Pięć minut: czytanie dwustronicowego dowodu bez dotknięcia myszy jest normalne, kwadrans – nie.
IDLE_SECONDS = 300

#: Najwięcej, ile jeden sygnał może dopisać do licznika. Patrz docstring modułu.
MAX_HEARTBEAT_GAP = 90


def heartbeat(review: Review, *, now=None) -> ReviewWorkLog:
    """Odbiera jeden sygnał życia i dopisuje do licznika czas, który upłynął od poprzedniego.

    Pierwszy sygnał zakłada licznik i **nic nie dolicza**: nie wiemy jeszcze, od kiedy trwa praca,
    a przyjęcie czegokolwiek byłoby zgadywaniem. Każdy kolejny dokłada odstęp przycięty do
    ``MAX_HEARTBEAT_GAP``.

    ``select_for_update`` na wierszu licznika: dwie karty z tą samą recenzją (a to jest zwykła
    sytuacja – recenzent otwiera pracę obok porównania ocen) wysyłają sygnały równolegle i bez
    blokady jedna nadpisałaby przyrost drugiej.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        log = ReviewWorkLog.objects.select_for_update().filter(review=review).first()
        if log is None:
            return ReviewWorkLog.objects.create(
                review=review, started_at=moment, last_seen_at=moment, seconds=0
            )
        gap = int((moment - log.last_seen_at).total_seconds())
        # Ujemny odstęp znaczy sygnał „z przeszłości” (przestawiony zegar, sygnał ``sendBeacon``
        # dostarczony po czasie). Czasu nie odejmujemy – licznik ma rosnąć albo stać.
        log.seconds += max(0, min(gap, MAX_HEARTBEAT_GAP))
        log.last_seen_at = moment
        log.save(update_fields=["seconds", "last_seen_at"])
        return log


def review_seconds(review: Review) -> int:
    """Zmierzony czas jednej recenzji w sekundach. Brak licznika = zero, a nie ``None``.

    Zero znaczy „nikt nie pracował nad tą recenzją w oknie przeglądarki” – recenzja sprzed
    wprowadzenia pomiaru, oceniona z wydruku albo wystawiona z wyłączonym JavaScriptem. Ekran
    rozróżnia to od „zmierzonego zera” samym tekstem („brak pomiaru”), a nie inną wartością.
    """
    log = getattr(review, "work_log", None)
    return int(log.seconds) if log is not None else 0


def format_duration(seconds: int) -> str:
    """Sekundy jako „1 h 12 min”, „12 min” albo „poniżej minuty”.

    Sekund nie pokazujemy nigdzie poza tym ostatnim przypadkiem: pomiar jest z natury przybliżony
    (sufit doliczenia, wykrywanie bezczynności), a „1 h 12 min 37 s” obiecywałoby dokładność,
    której ten licznik nie ma.
    """
    total = max(0, int(seconds))
    if total < 60:
        return "poniżej minuty"
    hours, minutes = divmod(total // 60, 60)
    if hours == 0:
        return f"{minutes} min"
    if minutes == 0:
        return f"{hours} h"
    return f"{hours} h {minutes} min"


def reviewer_seconds(stage) -> dict[int, int]:
    """Zmierzony czas pracy w etapie, w rozbiciu na recenzentów: ``{committee_member_id: sekundy}``.

    Jeden agregat na cały ekran, a nie zapytanie na wiersz: tabela postępu ma tyle wierszy, ilu
    jest aktywnych recenzentów, a etap finału ma tysiące recenzji. Recenzenci bez pomiaru po prostu
    nie mają klucza w wyniku – wywołujący traktuje ich jak zero (``dict.get(pk, 0)``).

    Do sumy wchodzą **wszystkie** recenzje etapu, także anulowane: czas nad pracą, którą koordynator
    potem odebrał, też był czasem pracy, a planowanie obciążenia interesuje właśnie włożony wysiłek.
    """
    rows = (
        ReviewWorkLog.objects.filter(review__submission__entry__stage=stage)
        .values("review__reviewer_id")
        .annotate(total=Sum("seconds"))
    )
    return {row["review__reviewer_id"]: int(row["total"] or 0) for row in rows}
