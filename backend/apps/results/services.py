"""Logika domenowa wyników etapu: przeliczenie, kwalifikacja i publikacja (T-07).

Widoki tylko orkiestrują. Zasady wspólne dla modułu:

- **snapshot kontra dane bieżące**: publikacja zamraża tabelę. Wszystko, co widzi publiczność,
  pochodzi z ``ResultsPublication.snapshot``; serwisy liczące dotykają bazy tylko w momencie
  publikacji albo podglądu koordynatora. Uczestnik w ``me/results/`` widzi natomiast swoje punkty
  **na żywo** wraz z ``published_total`` i znacznikiem ``differs_from_published`` (PROJEKT.md 2.4),
- **RODO**: snapshot przechodzi przez ``_display_name`` i zawiera wyłącznie ``rank``, ``display``,
  ``district`` (tylko przy ``CODE``), ``points``, ``total`` i ``qualified``. Nigdy e-maila, roku
  urodzenia ani id użytkownika; imię i nazwisko wyłącznie przy ``FULL``, tylko w finale, tylko dla
  laureata i tylko za zgodą uczestnika (oraz opiekuna, jeśli uczestnik jest niepełnoletni),
- **kolejność w czasie**: progi i publikacja liczą się dopiero po zamknięciu okna reklamacji
  (PROJEKT.md 2.4: „nigdy wcześniej”). Podgląd koordynatora (``compute``) wolno robić zawsze,
- **brak N+1**: przeliczenie etapu to stała liczba zapytań niezależnie od liczby wpisów – wpisy,
  zadania i zgłoszenia czytamy hurtem, a sumy składamy w Pythonie,
- audyt nigdy nie zawiera danych osobowych: w ``diff`` idą liczniki i identyfikatory,
- czas zawsze przez ``timezone.now()``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from django.db import transaction
from django.db.models import Count, Max, Prefetch
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.consents import is_minor
from apps.competitions.models import (
    ComponentKind,
    ManualQualification,
    PipelineStep,
    Problem,
    QualificationMode,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageKind,
    TieBreakKey,
    TransitionGroupBy,
    TransitionMode,
)
from apps.competitions.scoring import problem_maximum, stage_free_values
from apps.competitions.services import entry_owner, stage_scoring, weighted_scoring_enabled
from apps.core.api import DomainError
from apps.core.models import audit
from apps.core.points import POINTS_QUANTUM, WHOLE_POINTS, points_json, round_points, to_points
from apps.grading.models import Review, ReviewStatus
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.notifications import notify_results_published

from .models import Anonymization, ResultsPublication

logger = logging.getLogger(__name__)

#: Kolejność etapów edycji. Kwalifikacja przenosi uczestnika do następnego – finał nie ma następcy.
#: ``StageKind.TRAINING`` **nie ma** na tej liście i to jest cała reguła „trening jest poza
#: kwalifikacją”: etap treningowy nie ma następnego etapu i nigdy nie jest niczyim następnym.
STAGE_ORDER = (StageKind.ELIM, StageKind.DISTRICT, StageKind.FINAL)

#: Przełącznik, za którym stoi **cała** dwudrożność kwalifikacji w tym module
#: (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2). Nazwa jest jedna i jest tutaj, żeby literówka w niej
#: wywracała się w jednym miejscu, a nie w każdym wywołaniu ``has_feature`` – ten sam zabieg, co
#: ``BRANDING_FLAG`` w ``apps.tenancy.branding``. Wyłączona (i taka jest dla Konkursu #1) znaczy,
#: że kolejność etapów czytamy z ``STAGE_ORDER``, a próg z ``QualificationRule`` – czyli dokładnie
#: tą samą drogą i tym samym kodem, co przed etapem 2.
PROCESS_EDITOR_FLAG = "process_editor"

#: Przełącznik osobnych rankingów i progów per kategoria (§ 1.2.4). Wyłączony (i taki jest dla
#: Konkursu #1) znaczy: jedna tabela, wiersz bez ani jednego nowego klucza, snapshot bez ani
#: jednego nowego pola. Kategorie i przebieg z danych są **osobnymi** flagami z rozmysłem –
#: konkurs bywa dwukategorialny bez przepisywania przebiegu i odwrotnie.
CATEGORIES_FLAG = "categories"

#: Przełącznik zgłoszeń drużynowych (§ 1.2.3). W tym module rozstrzyga **wyłącznie** o tym, czy
#: odczyt wpisów dociąga drużynę jednym złączeniem – suma etapu chodzi po wpisach i drużyny nie
#: zauważa. Wyłączony (i taki jest dla Konkursu #1) znaczy: ani jedno złączenie więcej.
TEAM_ENTRIES_FLAG = "team_entries"

#: Stany, w których ocena zgłoszenia jeszcze trwa. Etap z takim zgłoszeniem nie da się przeliczyć:
#: suma punktów byłaby chwilowa, a opublikowana tabela musi być ostateczna (PROJEKT.md 2.4).
UNFINISHED_STATUSES = (
    SubmissionStatus.IN_REVIEW,
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
)
#: Ile pseudonimów wchodzi do komunikatu błędu. Etap finału ma tysiące prac – lista bez limitu
#: zamieniłaby komunikat w zrzut tabeli.
MAX_REPORTED_CODES = 20

#: Minimalna liczba uczestników jednej szkoły w etapie, przy której wolno pokazać „inicjały, szkoła”.
#: Poniżej progu para (inicjały, szkoła) wskazuje konkretną osobę – wtedy wiersz spada do pseudonimu
#: (k-anonimowość, PROJEKT.md 2.4).
MIN_SCHOOL_GROUP = 3

#: Wiek, od którego uznajemy uczestnika za pełnoletniego przy zgodzie na publikację nazwiska,
#: gdy znamy **wyłącznie rocznik** (profile sprzed wydania 0.30.0). 19, a nie 18, i to jest
#: zaokrąglenie w stronę ochrony: osoba, która skończy 18 lat w tym roku, przez część roku jeszcze
#: ich nie ma, a dnia urodzin w takim wierszu nie ma wcale.
#:
#: Uczestnik z pełną datą urodzenia nie przechodzi przez tę stałą w ogóle – dla niego pytanie
#: „czy jest pełnoletni” ma odpowiedź dokładną i wydaje ją ``apps.accounts.consents.is_minor``,
#: to samo miejsce, które rozstrzyga o zgodzie opiekuna przy rejestracji. Dwie różne odpowiedzi
#: na to samo pytanie w dwóch miejscach systemu byłyby gorsze od jednej niedokładnej.
ADULT_AGE = 19


# --- błędy domenowe ---------------------------------------------------------------------------


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


# --- flagi konkursu ----------------------------------------------------------------------------
#
# Trzy funkcje i ani jednego ``has_feature`` poza nimi – to jest cała reguła § 1.0 (c) zapisana
# w kodzie. Flagę czyta się **raz, możliwie wysoko**: na wejściu do przeliczenia albo na wejściu
# do kwalifikacji, nigdy w pętli po wierszach i nigdy w szablonie. Wyłączona flaga ma dać kod
# sprzed etapu 2 co do zapytania i co do wiersza, a nie „nową logikę ustawioną tak, żeby wyszło
# to samo”.


def competition_of(stage: Stage):
    """Konkurs, do którego należy etap – drogą przez edycję.

    Etap nie ma własnej kolumny konkursu i mieć jej nie będzie: druga droga do tej samej prawdy
    to druga okazja do rozjazdu (``docs/UNIWERSALNY-ETAP-1.md`` § 3.4, § 1.0 (a) etapu 2). Funkcja
    istnieje po to, żeby ta droga była zapisana **raz**, a nie powtarzana w każdym odczycie flagi.
    """
    return stage.edition.competition


def process_editor_enabled(competition=None) -> bool:
    """Czy ten konkurs czyta przebieg zawodów z danych, czy ze stałych w kodzie.

    **Jedyne** wejście do flagi ``process_editor`` w całym module wyników (§ 1.0 (c)): decyzja
    „którą drogą” zapada tu, raz na czynność, a nie w pętli po wierszach i nie w szablonie.

    Brak konkursu to nie jest „organizator wyłączył funkcję”, tylko „nie wiadomo, czyj to etap” –
    a odpowiedź w obu wypadkach ma być ta sama: zachowanie sprzed etapu 2.
    """
    return competition is not None and competition.has_feature(PROCESS_EDITOR_FLAG)


def categories_enabled(competition=None) -> bool:
    """Czy tabela wyników tego konkursu dzieli się na kategorie. **Jedyne** wejście do flagi.

    Odpowiedź ``False`` znaczy dokładnie tyle, co przed etapem 2: jeden ranking, wiersz bez klucza
    ``category`` i snapshot bez nowego pola. Ta sama reguła odwrotu, co wyżej – brak konkursu to
    „nie wiadomo, czyja to tabela”, a nie „organizator wyłączył kategorie”.
    """
    return competition is not None and competition.has_feature(CATEGORIES_FLAG)


def team_entries_enabled(competition=None) -> bool:
    """Czy w tym konkursie właścicielem wpisu bywa drużyna. **Jedyne** wejście do flagi tutaj.

    Odpowiedź rozstrzyga wyłącznie o **koszcie** odczytu: o właścicielu wpisu rozstrzygają dane
    wiersza (``competitions.services.entry_owner``), a nie przełącznik. Konkurs bez drużyn nie ma
    płacić za nie ani jednym złączeniem – stąd ta funkcja, a nie ``select_related("team")``
    dopisane na stałe (§ 0.1, § 5.6).
    """
    return competition is not None and competition.has_feature(TEAM_ENTRIES_FLAG)


# --- brama czasowa i blokada etapu -------------------------------------------------------------


def _assert_appeal_window_closed(stage: Stage) -> None:
    """Progi i publikacja dopiero po zamknięciu okna reklamacji (PROJEKT.md 2.4).

    Reklamacja może zmienić ``FinalGrade``, a więc i sumę punktów. Kwalifikacja policzona przy
    otwartym oknie musiałaby zostać cofnięta – a status „zakwalifikowany”, raz ogłoszony, jest
    obietnicą wobec uczestnika. Podgląd (``compute_stage_results``) tej bramy nie ma: to robocza
    tabela koordynatora, która niczego nie ogłasza.

    Etap treningowy jest z tej bramy wyjęty. Jego okno reklamacji to data-wartownik
    (``TRAINING_DEADLINE``, rok 2099) wpisana tylko po to, żeby oś czasu przeszła walidację – brama
    czekałaby na nią siedemdziesiąt lat i cała ścieżka „recenzje → wyniki” byłaby w piaskownicy
    nieprzejezdna. Nie ma tu też czego chronić: trening nikogo nie kwalifikuje (``next_stage_of``
    zwraca ``None``), a jego tabela jest podpisana odznaką „trening”.
    """
    if stage.is_training:
        return
    if stage.appeal_window_closes_at is None:
        return
    now = timezone.now()
    if now < stage.appeal_window_closes_at:
        raise _conflict(
            "Okno reklamacji jest jeszcze otwarte "
            f"(do {stage.appeal_window_closes_at.isoformat()}). Progi liczymy po jego zamknięciu.",
            "APPEAL_WINDOW_OPEN",
        )


def _locked_stage(stage: Stage) -> Stage:
    """Etap zablokowany do końca transakcji (``SELECT ... FOR UPDATE``).

    Dwie równoległe publikacje tego samego etapu (koordynator klika dwa razy, panel i API naraz)
    liczyłyby progi na tych samych danych i zapisywały dwa różne snapshoty. Blokada wiersza etapu
    ustawia je w kolejkę. Zwracamy świeży obiekt – stan z żądania mógł się zestarzeć.
    """
    return Stage.objects.select_for_update().get(pk=stage.pk)


# --- przeliczenie wyników ---------------------------------------------------------------------


def _latest_submissions(stage: Stage) -> dict[tuple[int, int], Submission]:
    """Najnowsza *nieodrzucona* wersja zgłoszenia dla każdej pary (wpis, zadanie).

    ``REJECTED_INFECTED`` jest pomijane: wersja odrzucona przez antywirusa nigdy nie weszła do
    oceniania, więc liczy się ostatnia wersja przed nią (a jeśli takiej nie ma – brak zgłoszenia,
    czyli 0 punktów). Jedno zapytanie na cały etap; ``final_grade`` przez ``select_related``
    (odwrotna strona relacji jeden-do-jednego), żeby suma nie robiła zapytania na wiersz.
    """
    rows = (
        Submission.objects.filter(entry__stage=stage)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("final_grade")
        .order_by("entry_id", "problem_id", "-version")
    )
    latest: dict[tuple[int, int], Submission] = {}
    for submission in rows:
        latest.setdefault((submission.entry_id, submission.problem_id), submission)
    return latest


def _blocks_finalization(submission: Submission) -> bool:
    """Czy to zgłoszenie nie pozwala jeszcze zamknąć tabeli wyników etapu.

    Blokuje każda praca w ocenianiu oraz **każda praca bez ``FinalGrade``** – także ta w stanie
    ``FINAL``. Praca oddana i nieoceniona to nie „zero punktów”, tylko brakująca ocena: cicha zamiana
    takiej luki na 0 zaniżyłaby sumę i mogła wyrzucić kogoś z progu, a błąd wyszedłby dopiero po
    ogłoszeniu wyników. Lepiej 409 z listą prac do dokończenia.
    """
    if submission.status in UNFINISHED_STATUSES:
        return True
    return getattr(submission, "final_grade", None) is None


def _assert_finalized(pending_codes: list[str]) -> None:
    if not pending_codes:
        return
    listed = sorted(set(pending_codes))
    shown = ", ".join(listed[:MAX_REPORTED_CODES])
    suffix = f" (+{len(listed) - MAX_REPORTED_CODES})" if len(listed) > MAX_REPORTED_CODES else ""
    error = _conflict(
        f"Ocenianie etapu nie jest zakończone. Nierozliczone prace: {shown}{suffix}.",
        "STAGE_NOT_FINALIZED",
    )
    # Lista pseudonimów także maszynowo – klient (panel koordynatora) nie musi parsować zdania.
    error.public_codes = listed
    raise error


def _category_fields(entry) -> dict:
    """Trzy klucze kategorii dokładane do wiersza – i ani jednego więcej.

    Rozdzielone z rozmysłem, bo czytają je trzy różne warstwy i każda potrzebuje czego innego:
    ``category_id`` grupuje (ranking i reguła przejścia), ``category`` jest **etykietą** do
    wyświetlenia w tabeli (tak samo, jak ``district`` jest etykietą, a nie slugiem), a
    ``category_position`` ustawia grupy w kolejności organizatora.

    Wpis bez kategorii w konkursie, który kategorie ma, dostaje puste wartości, a nie brak kluczy:
    tabela ma wtedy jedną grupę „bez kategorii”, a nie wiersze wypadające z rankingu.
    """
    category = entry.category
    return {
        "category_id": entry.category_id,
        "category": category.name if category is not None else "",
        "category_position": category.position if category is not None else 0,
    }


def _group_order(row: dict) -> tuple[int, int]:
    """Miejsce grupy w tabeli: kolejność ustawiona przez organizatora, a przy remisie identyfikator.

    Wiersz bez kategorii (czyli **każdy** wiersz Konkursu #1) daje ``(0, 0)``, więc wszystkie
    wiersze są w jednej grupie i porządek tabeli jest dokładnie ten, co przed etapem 2. Kolejność
    bierzemy z ``Category.position``, a nie z nazwy: „podstawowa” przed „ponadpodstawową” to
    decyzja organizatora, a nie przypadek alfabetu.
    """
    return (row.get("category_position") or 0, row.get("category_id") or 0)


# --- rozstrzyganie remisów (§ 1.2.6 c) -----------------------------------------------------------
#
# Dziś remis znaczy **to samo miejsce** i nie ma żadnego kryterium, które by go rozstrzygało;
# ``public_code`` układa wiersze wewnątrz remisu, ale niczego nie rozstrzyga – jest porządkiem
# powtarzalności wydruku. Etap 2 tego nie zmienia, tylko dokłada obok **listę** kryteriów, którą
# etap może mieć albo nie mieć (``competitions.TieBreak``). Etap bez ani jednego kryterium – czyli
# **każdy** etap Olimpiady Kwantowej – nie płaci za nie ani jednego zapytania i dostaje dokładnie
# dzisiejszą tabelę: ta sama kolejność wierszy i te same miejsca.
#
# Kryteria wchodzą do klucza sortowania **między** sumę a ``public_code`` i w tej samej kolejności
# rozstrzygają o wspólnym miejscu: dwa wiersze dzielą miejsce wtedy i tylko wtedy, gdy są równe na
# sumie i na każdym kryterium. Wartości są liczbami (czas oddania też – w sekundach epoki), bo
# kierunek „malejąco/rosnąco” nakłada się wtedy zmianą znaku, a jeden klucz sortowania jest jedyną
# gwarancją, że porządek wierszy i podział na miejsca nie mogą się rozejść.


@dataclass(frozen=True)
class TieBreakSpec:
    """Jedno kryterium rozstrzygania remisów, odczytane z bazy i gotowe do porównania w pamięci.

    Obiekt istnieje po to, żeby ``_rank_rows`` nie zadało **ani jednego** zapytania: wszystko, co
    kryterium potrzebuje z bazy (numer zadania, identyfikator komponentu, pełne wyniki zadań, czasy
    oddania prac), jest tu policzone raz na etap, a nie raz na wiersz.
    """

    key: str
    descending: bool = True
    #: Klucz w ``row["points"]`` dla ``PROBLEM_SCORE`` – numer zadania jako tekst, bo takim kluczem
    #: opisuje punkty ``compute_stage_results``.
    problem_number: str = ""
    #: Klucz w ``row["components"]`` dla ``COMPONENT_SCORE`` – identyfikator komponentu jako tekst.
    component_id: str = ""
    #: Pełny wynik każdego zadania (``SOLVED_COUNT``), w tej samej postaci, w jakiej leży w wierszu.
    full_scores: dict[str, Decimal] = field(default_factory=dict)
    #: Czas oddania ostatniej pracy wpisu w sekundach epoki (``SUBMITTED_AT``).
    last_submission: dict[int, float] = field(default_factory=dict)

    def value_of(self, row: dict) -> int | float | None:
        """Wartość kryterium dla jednego wiersza albo ``None``, gdy wiersz jej nie ma.

        ``None`` znaczy „nie ma czym rozstrzygnąć” i **zawsze** ląduje na końcu – niezależnie od
        kierunku. Brak wartości nie jest ani najlepszym, ani najgorszym wynikiem: to brak danych,
        a uczestnik bez danych nie ma wyprzedzać tego, o kim wiadomo.
        """
        points = row.get("points") or {}
        if self.key == TieBreakKey.HIGHEST_SINGLE:
            return max(points.values()) if points else None
        if self.key == TieBreakKey.PROBLEM_SCORE:
            return points.get(self.problem_number)
        if self.key == TieBreakKey.COMPONENT_SCORE:
            return (row.get("components") or {}).get(self.component_id)
        if self.key == TieBreakKey.SOLVED_COUNT:
            return sum(1 for number, full in self.full_scores.items() if points.get(number, -1) >= full)
        if self.key == TieBreakKey.SUBMITTED_AT:
            return self.last_submission.get(row["entry_id"])
        # ``NONE`` nie dochodzi tutaj nigdy: ``tie_break_keys`` ucina na nim listę. Gdyby doszło,
        # odpowiedź „brak wartości” jest jedyną bezpieczną – wszyscy równi, czyli wspólne miejsce.
        return None


def _full_scores(stage: Stage) -> dict[str, Decimal]:
    """Pełny wynik każdego zadania etapu – w tej samej postaci, w jakiej leży w wierszu wyników.

    Wiersz niesie ocenę **wystawioną przez recenzenta**, czyli wartość z bazy po odjęciu
    przesunięcia skali (``StageScoring.score``, § 1.2.6 b) – i to jest postać, w której stoi
    ``max_value`` skali, bo skalę wpisuje organizator swoimi liczbami. Pełny wynik musi być w tej
    samej postaci, inaczej „zadanie zrobione na maksa” nie zostałoby zliczone ani razu. Zadanie
    z własną skalą przesunięciu etapu nie podlega – tak samo czyta to
    ``competitions.services.stage_scoring``. Przy ``offset = 0`` (czyli w całym Konkursie #1)
    obie postaci są tą samą liczbą.

    Maksimum podaje ``competitions.scoring.problem_maximum`` – ta sama reguła, która sprawdza
    ocenę – więc zadanie z samym maksimum (tryb dowolny, wydanie 0.35.0) ma tu swoje 12,5, a nie
    maksimum skali etapu.
    """
    full: dict[str, Decimal] = {}
    for problem in stage.problems.all():
        maximum = problem_maximum(stage, problem)
        if maximum is not None:
            full[str(problem.number)] = maximum
    return full


def _last_submission_times(stage: Stage) -> dict[int, float]:
    """Czas oddania **ostatniej** pracy każdego wpisu, w sekundach epoki. Jedno zapytanie na etap.

    Wersja odrzucona przez antywirusa nie jest oddaniem pracy – tak samo czyta to
    ``_latest_submissions``: taka wersja nigdy nie weszła do oceniania. Wpis bez ani jednej pracy
    nie ma tu wiersza i kryterium odpowie dla niego ``None``, czyli „na koniec”.
    """
    rows = (
        Submission.objects.filter(entry__stage=stage)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .values("entry_id")
        .annotate(last=Max("submitted_at"))
    )
    return {row["entry_id"]: row["last"].timestamp() for row in rows}


def tie_break_keys(stage: Stage, *, competition=None) -> tuple[TieBreakSpec, ...]:
    """Kryteria rozstrzygania remisów etapu, w kolejności organizatora i z gotowymi wartościami.

    Pusta krotka znaczy „układaj tabelę dokładnie jak przed etapem 2” i jest odpowiedzią w trzech
    wypadkach: flaga ``weighted_scoring`` wyłączona (czyli w całym Konkursie #1), etap bez ani
    jednego wiersza ``TieBreak``, albo lista zaczynająca się od ``NONE``. Przy wyłączonej fladze
    funkcja nie robi **ani jednego zapytania** – pyta o flagę, zanim spojrzy na bazę (§ 5.6).

    Flagę czyta ``competitions.services.weighted_scoring_enabled`` i nikt tu poza nią (§ 1.0 (c)):
    wagi, punkty ujemne i remisy są jedną decyzją organizatora, więc mają jedno wejście. Konkurs
    wolno podać, gdy wołający i tak go ma – przeliczenie wyników ma – żeby droga przez edycję nie
    była przechodzona drugi raz.

    ``NONE`` ucina listę **wraz z sobą**: „po tych kryteriach już nie rozstrzygamy” znaczy, że
    wiersze równe na wcześniejszych dzielą miejsce, a nie że rozstrzyga je cokolwiek dalszego.
    Kryteria za nim zostają w bazie i wracają do gry, gdy koordynator przestawi kolejność – to jest
    ta sama decyzja, co przy ``off_pipeline``: wiersz wyjęty z toru, a nie skasowany.
    """
    if competition is None:
        competition = competition_of(stage)
    if not weighted_scoring_enabled(competition):
        return ()
    rules: list[TieBreakSpec] = []
    full_scores: dict[str, Decimal] | None = None
    last_submission: dict[int, float] | None = None
    for rule in stage.tie_breaks.select_related("problem").order_by("position", "id"):
        if rule.key == TieBreakKey.NONE:
            break
        if rule.key == TieBreakKey.SOLVED_COUNT and full_scores is None:
            full_scores = _full_scores(stage)
        if rule.key == TieBreakKey.SUBMITTED_AT and last_submission is None:
            last_submission = _last_submission_times(stage)
        rules.append(
            TieBreakSpec(
                key=rule.key,
                descending=rule.descending,
                problem_number=str(rule.problem.number) if rule.problem_id else "",
                component_id=str(rule.component_id) if rule.component_id else "",
                full_scores=full_scores or {},
                last_submission=last_submission or {},
            )
        )
    return tuple(rules)


def _tie_sort_key(row: dict, tie_breaks: tuple[TieBreakSpec, ...]) -> tuple:
    """Część klucza sortowania pochodząca z kryteriów remisu. Bez kryteriów – pusta krotka.

    Każde kryterium daje parę ``(obecność, liczba)``: wartość obecna ma ``0`` i idzie przed brakiem
    (``(1, 0)``) niezależnie od kierunku, a kierunek „malejąco” nakłada się zmianą znaku. Para, a nie
    sama liczba, bo inaczej „brak wyniku” musiałby udawać którąś z wartości – a udawać ma nie czego:
    ani zera (to jest wynik), ani nieskończoności (to nie jest liczba punktów).
    """
    return tuple(
        (0, -value if spec.descending else value) if (value := spec.value_of(row)) is not None else (1, 0)
        for spec in tie_breaks
    )


def _place_key(row: dict, tie_breaks: tuple[TieBreakSpec, ...]) -> tuple:
    """Co musi być równe, żeby dwa wiersze dzieliły miejsce: suma i **każde** kryterium.

    Bez kryteriów wychodzi jednoelementowa krotka z samą sumą, czyli dokładnie dzisiejsza reguła
    „ten sam wynik to to samo miejsce”. Klucz jest liczony z tych samych wartości, co klucz
    sortowania, żeby porządek wierszy i podział na miejsca nie mogły powiedzieć czegoś innego.
    """
    return (row["total"], *(spec.value_of(row) for spec in tie_breaks))


def _rank_rows(
    rows: list[dict], group_by: str | None = None, tie_breaks: tuple[TieBreakSpec, ...] = ()
) -> list[dict]:
    """Nadaje miejsca: malejąco po sumie, remis = to samo miejsce (1, 1, 3).

    Porządek wewnątrz remisu jest po ``public_code``, żeby tabela była powtarzalna – dwie
    publikacje tych samych danych muszą dać ten sam plik. ``public_code`` zostaje **ostatnim**
    kluczem sortowania i nie jest kryterium rozstrzygania – jest porządkiem powtarzalności wydruku.

    ``group_by`` (etap 2, § 1.2.4) wskazuje klucz wiersza, po którym tabela dzieli się na osobne
    rankingi – dziś wyłącznie ``"category_id"``. ``None`` znaczy jedna tabela i jest **jedynym**
    stanem Konkursu #1: wtedy wszystkie wiersze wpadają do jednej grupy, ``_group_order`` oddaje
    dla każdego to samo, a lista wychodzi z tej funkcji identyczna co do wiersza i co do miejsca,
    jak przed etapem 2. Grupy nie są liczone osobną pętlą właśnie po to, żeby nie było dwóch
    implementacji „miejsca w tabeli”, które mogłyby się rozejść na remisie.

    Miejsca wewnątrz grupy liczą się **od 1**: uczestnik kategorii „podstawowa” ma być pierwszy
    w swojej kategorii, a nie dwusetny w tabeli, której nikt w tej postaci nie ogłasza.

    ``tie_breaks`` (etap 2, § 1.2.6 c) to kryteria rozstrzygania remisów etapu, gotowe z
    ``tie_break_keys``. Pusta krotka – jedyny stan Konkursu #1 – znaczy dokładnie dzisiejszą regułę:
    ta sama suma to to samo miejsce. Z kryteriami wspólne miejsce dostają wyłącznie wiersze równe
    **na sumie i na każdym kryterium**; kryteria wchodzą do klucza sortowania między sumę
    a ``public_code``, który zostaje ostatnim kluczem także wtedy. Funkcja nie robi tu ani jednego
    zapytania – wszystko, czego kryteria potrzebują z bazy, niesie ``TieBreakSpec``.
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            _group_order(row),
            -row["total"],
            _tie_sort_key(row, tie_breaks),
            row["public_code"],
        ),
    )
    #: Wartownik, bo ``None`` jest poprawnym kluczem grupy (wiersz bez kategorii w konkursie,
    #: który kategorie ma) i nie da się go użyć jako „jeszcze nie zaczęliśmy”.
    unset = object()
    group: object = unset
    start = rank = 0
    previous_place: tuple | None = None
    for index, row in enumerate(ordered):
        key = row.get(group_by) if group_by else None
        if group is unset or key != group:
            group, start, previous_place = key, index, None
        place = _place_key(row, tie_breaks)
        if previous_place is None or place != previous_place:
            rank = index - start + 1
            previous_place = place
        row["rank"] = rank
    return ordered


def _is_adult(birth_date, birth_year: int | None, today) -> bool:
    """Czy uczestnik jest pełnoletni – dokładnie, gdy znamy datę; „na pewno”, gdy sam rocznik.

    Z pełną datą odpowiedź wydaje ``consents.is_minor``: pełnoletni jest ten, kto **skończył**
    18 lat, licząc kalendarzowo i z 29 lutego włącznie. Bez daty zostaje stara reguła rocznikowa
    (``ADULT_AGE``), bo dnia urodzin w takim wierszu nie ma i nie będzie.

    Brak jednego i drugiego znaczy „nie pełnoletni”, czyli nazwisko do tabeli nie wejdzie bez
    zgody opiekuna. Przy nieznanym wieku to jedyna odpowiedź, którą da się obronić.
    """
    if birth_date is not None:
        return not is_minor(birth_date, today=today)
    if not birth_year:
        return False
    return today.year - int(birth_year) >= ADULT_AGE


def _quiz_scores(stage: Stage, *, preview: bool) -> dict[int, Decimal] | None:
    """Punkty z testu online per wpis – albo ``None``, gdy etap nie jest testem.

    Jedyne miejsce, w którym wyniki wiedzą o istnieniu ``apps.quiz``, i cała jego wiedza mieści
    się w jednym pytaniu i jednym słowniku. Rozróżnienie ``None`` (to nie jest etap testowy) od
    pustego słownika (test bez ani jednego podejścia) jest istotne: w drugim przypadku wszyscy
    dostają zero z testu, a nie sumę z zadań, których etap nie ma.

    Import jest lokalny, a nie w nagłówku modułu, i to jest świadoma cena. ``apps.quiz`` zależy od
    ``apps.competitions``, a ``apps.results`` od obu – import na górze nie tworzyłby dziś cyklu,
    ale wiązałby przeliczanie wyników z aplikacją testów na czas ładowania, dla etapów, które
    testu nie mają i mieć nie będą. Zależność w jednej funkcji jest też **widoczna**: to jest
    dokładnie ten szew, którym wyniki i testy da się kiedyś rozdzielić.

    Poza podglądem domykamy najpierw porzucone podejścia (``finalise_overdue``). Bez tego praca
    kogoś, komu padło łącze na ostatnim pytaniu, zostałaby w stanie „w trakcie” i weszłaby do
    protokołu jako zero, mimo że jego odpowiedzi leżą zapisane w bazie. W podglądzie tego nie
    robimy, bo podgląd (symulacja progu) biegnie na żądanie GET i nie ma prawa niczego zapisać –
    tam podejście trwające liczy się jako zero, tak samo jak nieoceniona praca w etapie pisemnym.
    """
    from apps.quiz import services as quiz_services

    if not quiz_services.is_quiz_stage(stage):
        return None
    if not preview:
        quiz_services.finalise_overdue(stage=stage)
    return quiz_services.stage_scores(stage)


# --- komponenty etapu (§ 1.2.3) ------------------------------------------------------------------
#
# Etap **bez ani jednego komponentu** nie wchodzi tu ani razu: ``compute_stage_results`` pyta o nie
# dopiero przy włączonej fladze ``process_editor``, a etap bez wierszy czyta ``Stage.format``
# dokładnie tak, jak czytał przed etapem 2. To nie jest trzecia gałąź obok „zadania albo test” –
# to jest **zamiana jednej osi na listę**: ta sama suma ocen zadań i ten sam szew z testem online
# (``apps.quiz.services.stage_scores``), tylko wybierane przez wiersz komponentu, a nie przez pole.

#: Rodzaje komponentów, których punkty biorą się z ``FinalGrade`` po zadaniach etapu – czyli tą samą
#: drogą i z tej samej pętli, co dzisiejsza suma. Różni je to, **czyja** to praca i kto ją wystawił
#: (uczestnik zdalnie, komisja na miejscu, drużyna), a nie arytmetyka.
#:
#: Konsekwencja, nazwana wprost: kilka takich komponentów w jednym etapie dzieli **jedną** sumę ocen
#: zadań. Rozbicie zadań między komponenty wymagałoby wskazania komponentu przy zadaniu, a takiego
#: pola nie ma i etap 2 go nie dokłada – etap o dwóch niezależnych zestawach zadań opisuje się dziś
#: dwoma krokami przebiegu, a nie dwoma komponentami.
GRADE_COMPONENT_KINDS = frozenset({ComponentKind.SUBMISSIONS, ComponentKind.ONSITE, ComponentKind.TEAM})


def _components_of(stage: Stage) -> tuple:
    """Komponenty etapu w kolejności ``position``. Pusta krotka znaczy „czytaj ``Stage.format``”."""
    return tuple(stage.components.all())


def _quiz_component_scores(stage: Stage, *, preview: bool) -> dict[int, Decimal]:
    """Punkty z testu dla **komponentu**, czyli bez pytania o ``Stage.format``.

    Szew z ``apps.quiz`` jest ten sam, co w ``_quiz_scores`` (``stage_scores`` i ``finalise_overdue``
    poza podglądem) i celowo nie jest z nią scalony: tamta funkcja odpowiada na pytanie „czy ten
    etap **jest** testem”, a ta na „ile punktów dał test, który jest **jedną z form** tego etapu”.
    Scalenie ich znaczyłoby, że etap z komponentem testowym musi mieć ``format=QUIZ`` – czyli że
    komponenty nie zniosły wykluczalności, tylko ją przepisały.

    Etap bez testu dostaje pusty słownik (``stage_scores`` sam tak odpowiada), więc komponent
    testowy w etapie bez testu daje wszystkim zero, a nie wywraca przeliczenia.
    """
    from apps.quiz import services as quiz_services

    if not preview:
        quiz_services.finalise_overdue(stage=stage)
    return quiz_services.stage_scores(stage)


def _component_sources(stage: Stage, components, *, preview: bool) -> dict[int, dict[int, int]]:
    """Punkty per wpis dla źródeł, które **nie** są sumą ocen zadań. Jedno zapytanie na źródło.

    Źródło, którego żaden komponent nie potrzebuje, nie jest w ogóle odpytywane – etap o samych
    komponentach pisemnych nie płaci ani jednego zapytania za istnienie testów online ani za
    istnienie rozmów.

    Klucz jest **komponentem**, a nie jego rodzajem, i to jest jedyna różnica wobec pierwszego
    kształtu tej funkcji (T35). Powód nazwany wprost: dwie rozmowy w jednym etapie – wstępna
    i finałowa – są dwoma komponentami o dwóch niezależnych wynikach, a klucz po rodzaju dałby im
    jeden wspólny słownik, czyli tę samą liczbę w dwóch kolumnach tabeli. Test online tego problemu
    nie ma (etap ma jeden zestaw pytań), więc jego mapa jest **tą samą** mapą pod każdym kluczem –
    zapytanie pada raz, niezależnie od liczby komponentów testowych.

    ``INTERVIEW`` czyta ``InterviewScore`` przez ``competitions.interviews.interview_scores_for``
    (T36, § 1.2.3): wpis bez wyniku **nie ma tam klucza**, a nie zero – różnicę między „komisja
    wpisała zero” a „komisja jeszcze nie wpisała” rozstrzyga dopiero ``StageComponent.required``
    (patrz ``_missing_interview_codes``). Import jest lokalny, tak samo jak przy teście online:
    moduł wyników czyta źródło punktów dopiero wtedy, gdy etap naprawdę ma taki komponent.
    """
    sources: dict[int, dict[int, int]] = {}
    quiz_scores: dict[int, Decimal] | None = None
    interview_scores: dict[int, dict[int, int]] | None = None
    for component in components:
        if component.kind == ComponentKind.QUIZ:
            if quiz_scores is None:
                quiz_scores = _quiz_component_scores(stage, preview=preview)
            sources[component.pk] = quiz_scores
        elif component.kind == ComponentKind.INTERVIEW:
            if interview_scores is None:
                from apps.competitions.interviews import interview_scores_for

                interview_scores = interview_scores_for(stage)
            sources[component.pk] = interview_scores.get(component.pk, {})
    return sources


def _missing_interview_codes(components, sources, rows) -> list[str]:
    """Pseudonimy wpisów, którym komisja nie wpisała punktów z **wymaganej** rozmowy.

    Ta sama reguła, co przy nierozliczonej pracy (``_blocks_finalization``) i ten sam skutek
    (``STAGE_NOT_FINALIZED``): brak wyniku nie jest zerem, tylko brakującą oceną, a cicha zamiana
    luki na zero zaniżyłaby sumę i mogła wyrzucić kogoś z progu. ``required=False`` znaczy dokładnie
    to, co mówi docstring ``StageComponent``: rozmowa nieobowiązkowa liczy się jako zero i nie
    trzyma całej tabeli za zakładnika.

    Rozmowa jest jedynym takim źródłem. Test online swojego braku nie zgłasza i zgłaszać nie ma –
    podejście, którego nikt nie zaczął, jest tam zerem od zawsze (``quiz.services.stage_scores``),
    a zmiana tej reguły byłaby zmianą zachowania etapu testowego, a nie dołożeniem rozmowy.
    """
    required = [c for c in components if c.required and c.kind == ComponentKind.INTERVIEW]
    if not required:
        return []
    missing: list[str] = []
    for component in required:
        scored = sources.get(component.pk, {})
        missing += [row["public_code"] for row in rows if row["entry_id"] not in scored]
    return missing


def _grades_block_finalization(components) -> bool:
    """Czy nierozliczona praca nadal zamyka tabelę wyników etapu.

    Bez komponentów: **zawsze**, czyli dokładnie jak dziś (``_blocks_finalization`` i
    ``STAGE_NOT_FINALIZED``). Z komponentami: tylko wtedy, gdy któryś komponent czytający oceny
    jest ``required``. Komponent nieobowiązkowy to zawody dodatkowe, w których nie każdy startuje –
    czekanie z całą tabelą na jego dokończenie byłoby braniem zakładnika za cudzą nieobecność.
    """
    if not components:
        return True
    return any(component.required and component.kind in GRADE_COMPONENT_KINDS for component in components)


def _round_half_up(value: Fraction, quantum: Decimal = WHOLE_POINTS) -> Decimal:
    """Ułamek na pełne punkty (albo na 0,01), połówka w górę – **ta sama** metoda, co ``apps.quiz.services``.

    Zaokrąglenie następuje **raz**, na samym końcu sumy (§ 1.2.6). Sumowanie idzie przez
    ``Fraction``, bo waga ``1/3`` zapisana jako ``0.333…`` dawałaby sumę zależną od kolejności
    dodawania – czyli tabelę wyników zmieniającą się przy ponownym przeliczeniu tych samych danych.

    ``quantum`` (wydanie 0.35.0) to pełny punkt w etapie „tylko ze skali” – dokładnie jak przed
    tym wydaniem – i 0,01 w etapie z dowolnymi wartościami (``_total_quantum``).
    """
    return round_points(Decimal(value.numerator) / Decimal(value.denominator), quantum)


def _total_quantum(stage: Stage) -> Decimal:
    """Krok zaokrąglenia sumy ważonej etapu: 0,01 w trybie dowolnym, pełny punkt w trybie skali."""
    return POINTS_QUANTUM if stage_free_values(stage) else WHOLE_POINTS


def _component_total(
    entry_id: int, components, sources, grades_total, quantum: Decimal = WHOLE_POINTS
) -> tuple[dict[str, Decimal], Decimal]:
    """Punkty komponentów jednego wpisu i ich ważona suma.

    Zwraca parę: słownik ``{id komponentu: punkty}`` (kolumny tabeli koordynatora i wsad do
    rozstrzygania remisów, T40) oraz sumę zaokrągloną do ``quantum`` – pełnych punktów albo, w etapie
    z dowolnymi wartościami, 0,01. Waga ``1/1`` na jedynym komponencie pisemnym daje liczbę
    **identyczną** z sumą ocen zadań – i to jest osobna asercja testu, a nie przypuszczenie.

    Źródła są kluczowane **komponentem** (``_component_sources``), więc dwa komponenty tego samego
    rodzaju dostają dwa niezależne wyniki – a nie jedną liczbę powtórzoną w dwóch kolumnach.
    """
    scores: dict[str, Decimal] = {}
    total = Fraction(0)
    for component in components:
        if component.kind in GRADE_COMPONENT_KINDS:
            score = to_points(grades_total)
        else:
            score = to_points(sources.get(component.pk, {}).get(entry_id, 0))
        scores[str(component.pk)] = score
        total += component.weight * Fraction(score)
    return scores, _round_half_up(total, quantum)


def _owner_fields(owner, participant, today) -> dict:
    """Ta część wiersza, która opisuje **właściciela** wpisu: uczestnika albo drużynę (§ 1.2.3).

    Klucze są te same dla obu rodzajów właściciela i to jest cała sztuczka: kwalifikacja, próg,
    snapshot i podgląd koordynatora czytają wiersz, a nie wpis, więc nie muszą wiedzieć, kto nim
    stoi. Wiersz uczestnika wychodzi stąd **co do klucza i co do wartości** taki, jak przed
    etapem 2 – Konkurs #1 nie ma ani jednej drużyny, więc druga gałąź nie wykonuje się tam nigdy.

    Drużyna nie ma nazwiska, wieku ani zgód na publikację i dlatego dostaje wartości „domyślnie
    zamknięte”: puste napisy i ``False``. Nazwa drużyny jedzie osobnym kluczem ``team_name``,
    którego wiersz uczestnika **nie ma** – po nim (i tylko po nim) ``_display_name`` poznaje, że
    podpisem tej pozycji jest nazwa składu, a nie pseudonim osoby.
    """
    if participant is None:
        return {
            "participant_id": None,
            "public_code": owner.public_code,
            "first_name": "",
            "last_name": "",
            "school": owner.school,
            # Drużyna bywa międzyszkolna i nie ma okręgu – pusty napis jest tu odpowiedzią
            # prawdziwą, a nie brakiem danych do uzupełnienia.
            "district": "",
            "publish_full_name": False,
            "guardian_consent": False,
            "is_adult": False,
            "team_name": owner.name,
        }
    return {
        "participant_id": participant.pk,
        "public_code": participant.public_code,
        "first_name": participant.user.first_name,
        "last_name": participant.user.last_name,
        "school": participant.school,
        # Etykieta, nie slug: snapshot jest danymi do wyświetlenia (tabela publiczna
        # i podgląd koordynatora czytają go dosłownie), a grupowanie po ``_district_key``
        # jest odporne na postać zapisu, bo normalizuje wielkość liter i spacje.
        "district": participant.get_district_display(),
        "publish_full_name": participant.publish_full_name,
        "guardian_consent": participant.guardian_consent,
        "is_adult": _is_adult(participant.birth_date, participant.birth_year, today),
    }


def compute_stage_results(stage: Stage, *, preview: bool = False) -> list[dict]:
    """Tabela wyników etapu: suma ``FinalGrade.score`` po najnowszych wersjach zgłoszeń.

    Brak zgłoszenia do zadania = 0 punktów. Zwraca wiersze **pełne** (z danymi osobowymi) – to
    materiał dla koordynatora i wsad do anonimizacji w ``publish_results``, nigdy odpowiedź
    publiczna. Zapisuje ``StageEntry.total_points`` jednym ``bulk_update``.

    Rzuca ``STAGE_NOT_FINALIZED`` (409), gdy którakolwiek najnowsza wersja jest jeszcze
    w ocenianiu – z listą pseudonimów prac do dokończenia.

    ``preview=True`` wyłącza **obie** te rzeczy naraz i jest przeznaczone dla symulacji progu
    (``apps.results.simulation``), która odpowiada na pytanie „co by było, gdyby”. Wyłączenie jest
    wspólne z rozmysłem: symulacja z definicji biegnie w trakcie oceniania, więc brama
    „ocenianie zakończone” zamknęłaby ją przez cały czas, kiedy jest potrzebna, a zapis
    ``StageEntry.total_points`` byłby skutkiem ubocznym zwykłego wejścia na stronę (żądanie GET).
    Praca bez oceny liczy się wtedy jako 0 punktów – i dlatego ekran symulacji **musi** napisać,
    ilu prac jeszcze nie rozliczono. Publikacja i kwalifikacja nigdy z tego trybu nie korzystają.

    **Kategorie (§ 1.2.4)** dokładają do wiersza trzy klucze i dzielą ranking – ale wyłącznie
    w konkursie, który ma flagę ``categories`` włączoną. Przy wyłączonej nie ma ani jednego
    nowego klucza w wierszu, ani jednego złączenia więcej w zapytaniu i ani jednej innej liczby
    w kolumnie „miejsce”. To jest wymaganie nadrzędne (§ 0.1), a nie optymalizacja.

    **Komponenty (§ 1.2.3)** są drugą, równoległą drogą sumowania i wchodzą dopiero wtedy, gdy
    konkurs ma flagę ``process_editor``, a etap ma choć jeden wiersz ``StageComponent``. Etap bez
    komponentów – czyli **każdy** etap Olimpiady Kwantowej – nie płaci za nie ani jednego zapytania
    i liczy się tą samą gałęzią, co przed etapem 2: suma ocen zadań albo, dla ``format=QUIZ``,
    punkty z testu **zamiast** niej.

    **Remisy (§ 1.2.6 c)** rozstrzyga lista ``TieBreak`` etapu i wchodzi ona wyłącznie tam, gdzie
    konkurs ma flagę ``weighted_scoring``, a etap – choć jeden wiersz. Etap bez kryteriów układa
    tabelę dokładnie jak dziś: ta sama suma to to samo miejsce, a ``public_code`` porządkuje wydruk.
    """
    competition = competition_of(stage)
    with_categories = categories_enabled(competition)
    components = _components_of(stage) if process_editor_enabled(competition) else ()
    # Kryteria remisu też czytamy **raz na przeliczenie**, a nie raz na wiersz – i przy wyłączonej
    # fladze ``weighted_scoring`` nie kosztuje to ani jednego zapytania (§ 1.2.6 c).
    tie_breaks = tie_break_keys(stage, competition=competition)
    problems = list(stage.problems.order_by("number", "id"))
    # Wagi zadań i przesunięcie skali – odczytane **raz na przeliczenie** i z gotowych obiektów,
    # więc przy włączonej fladze nie kosztują ani jednego zapytania, a przy wyłączonej wychodzą
    # stałą ``PLAIN_SCORING``, czyli dzisiejszym sumowaniem ``int`` (§ 1.2.6 a–b).
    scoring = stage_scoring(stage, competition=competition, problems=problems)
    entries = StageEntry.objects.filter(stage=stage).select_related("participant", "participant__user")
    if with_categories:
        # Złączenie, a nie zapytanie na wiersz – i tylko wtedy, gdy kategorie w ogóle istnieją.
        entries = entries.select_related("category")
    if team_entries_enabled(competition):
        # Drugi właściciel wpisu (§ 1.2.3) – tym samym zabiegiem i z tego samego powodu, co
        # kategorie wyżej: jedno złączenie zamiast zapytania na wiersz i wyłącznie w konkursie,
        # który drużyny w ogóle prowadzi.
        entries = entries.select_related("team")
    entries = list(entries.order_by("id"))
    latest = _latest_submissions(stage)
    # Etap z komponentami nie pyta o ``Stage.format`` w ogóle: o źródła punktów rozstrzyga lista,
    # a test online jest na niej jednym z rodzajów, a nie wykluczającą alternatywą.
    quiz_scores = None if components else _quiz_scores(stage, preview=preview)
    component_sources = _component_sources(stage, components, preview=preview) if components else {}
    # Krok zaokrąglenia sumy komponentów – czytany wyłącznie wtedy, gdy komponenty są, więc etap
    # bez nich nie płaci za to ani jednego zapytania (budżety ``tenancy/tests/test_invariants``).
    quantum = _total_quantum(stage) if components else WHOLE_POINTS
    grades_block = _grades_block_finalization(components)
    # Dzisiejsza data **lokalna** (Europe/Warsaw), liczona raz na cały etap: pełnoletność zmienia
    # się o północy czasu lokalnego, a nie UTC, więc ``timezone.now().date()`` przesuwałby urodziny
    # o kilka godzin w wybrane dni roku. Do wiersza trafia gotowa flaga, nigdy sama data ani
    # rocznik – wiek nie ma po co wędrować przez warstwy aż do serializera.
    today = timezone.localdate()

    pending_codes: list[str] = []
    rows: list[dict] = []
    for entry in entries:
        # Właściciel wpisu: uczestnik **albo** drużyna, jednym wejściem i bez czytania flagi
        # (§ 1.2.3). ``participant`` zostaje osobno, bo wiersz niesie o uczestniku rzeczy, których
        # drużyna nie ma – nazwisko, zgody, okręg – a dla wpisu drużynowego jest ``None``.
        owner = entry_owner(entry)
        participant = entry.participant
        points: dict[str, Decimal | int] = {}
        # Oceny **takie, jak leżą w bazie**, i wyłącznie te, które ktoś wystawił. Zadanie bez oceny
        # nie ma tu klucza i to jest różnica, której nie wolno zgubić: przy skali z punktami
        # ujemnymi „zero w bazie” znaczy „minus przesunięcie”, a brak pracy znaczy zero punktów.
        # Wartości są ``Decimal`` z kolumny dziesiętnej (wydanie 0.35.0) – bez rzutowania na
        # ``int``, które przed tym wydaniem było bezstratne, a dziś ucinałoby ocenę 4,25 do 4.
        raw: dict[int, Decimal] = {}
        for problem in problems:
            submission = latest.get((entry.pk, problem.pk))
            score = 0
            if submission is not None:
                if grades_block and _blocks_finalization(submission):
                    pending_codes.append(owner.public_code)
                grade = getattr(submission, "final_grade", None)
                if grade is not None:
                    raw[problem.pk] = grade.score
                    # Do tabeli idzie ocena **wystawiona** przez recenzenta, czyli wartość z bazy
                    # pomniejszona o przesunięcie skali. Bez flagi (i przy skali bez punktów
                    # ujemnych) jest to dokładnie liczba z kolumny ``score``.
                    score = scoring.score(problem.pk, raw[problem.pk])
            points[str(problem.number)] = score
        # Suma liczona **raz**, z ocen takich, jakie leżą w bazie: przesunięcie odejmuje
        # ``StageScoring.total``, a nie wołający, żeby nie dało się odjąć go dwa razy ani ani razu.
        # Bez flagi jest to dzisiejsze ``sum`` po ocenach, co do działania.
        total = scoring.total(raw)
        component_points: dict[str, int] | None = None
        if components:
            # Suma po komponentach **zastępuje** obie dzisiejsze ścieżki naraz: sumę ocen zadań
            # (która wchodzi tu jako punkty komponentu pisemnego) i punkty z testu (jako punkty
            # komponentu testowego). Nic nie jest liczone dwa razy – ``total`` z pętli wyżej jest
            # wejściem do tej funkcji, a nie składnikiem obok niej.
            component_points, total = _component_total(
                entry.pk, components, component_sources, total, quantum
            )
        elif quiz_scores is not None:
            # Etap w formie testu online nie ma zadań ani prac, więc pętla wyżej nic nie policzyła.
            # Suma przychodzi w całości z ``apps.quiz`` i **zastępuje** sumę z zadań, a nie dokłada
            # się do niej: gdyby etap miał jedno i drugie, byłby etapem o dwóch formach naraz –
            # a takiego stanu nie da się opisać ani w regulaminie, ani w tabeli wyników.
            # ``points`` zostaje pusty, bo kolumny tabeli wyników to zadania (``problem_numbers``),
            # a pytania testu są ich zbyt drobnym i zbyt licznym odpowiednikiem; rozbicie na
            # pytania stoi na własnym ekranie (``/coordinator/stages/<id>/quiz/results/``).
            total = quiz_scores.get(entry.pk, 0)
        row = {
            "entry_id": entry.pk,
            **_owner_fields(owner, participant, today),
            "status": entry.status,
            # Decyzja komitetu o kwalifikacji wbrew progowi (pusta = rozstrzyga próg).
            # Wędruje w wierszu, bo czytają ją trzy różne warstwy: kwalifikacja
            # (``manual_qualified``), snapshot (odznaka w ogłoszonej tabeli) i symulacja.
            "manual_qualification": entry.manual_qualification,
            "points": points,
            "total": total,
        }
        if with_categories:
            row |= _category_fields(entry)
        if component_points is not None:
            # Rozbicie na komponenty jest w wierszu **roboczym**, a nie w snapshocie: kolumny
            # ogłoszonej tabeli buduje ``build_snapshot`` z jawnej listy pól i ten klucz do niej
            # nie wchodzi. Czytają go podgląd koordynatora i rozstrzyganie remisów (T40).
            row["components"] = component_points
        rows.append(row)

    group_by = "category_id" if with_categories else None
    if preview:
        return _rank_rows(rows, group_by, tie_breaks)

    # Brak punktów z **wymaganej** rozmowy zamyka tabelę tak samo, jak nierozliczona praca – i tak
    # samo jak ona nie zatrzymuje podglądu ani symulacji progu (wyjście wyżej). Liczy się to po
    # pętli, a nie w niej: wiersz niesie już i identyfikator wpisu, i pseudonim.
    if components:
        pending_codes += _missing_interview_codes(components, component_sources, rows)

    _assert_finalized(pending_codes)

    changed = []
    by_entry = {row["entry_id"]: row for row in rows}
    for entry in entries:
        total = by_entry[entry.pk]["total"]
        if entry.total_points != total:
            entry.total_points = total
            changed.append(entry)
    if changed:
        StageEntry.objects.bulk_update(changed, ["total_points"])
    return _rank_rows(rows, group_by, tie_breaks)


# --- kwalifikacja -----------------------------------------------------------------------------


def _rule_for(stage: Stage):
    rule = getattr(stage, "qualification_rule", None)
    if rule is None:
        raise _conflict("Etap nie ma progu kwalifikacji.", "QUALIFICATION_RULE_MISSING")
    if rule.requires_min_points and rule.min_points is None:
        raise _conflict(f"Próg {rule.mode} wymaga min_points.", "QUALIFICATION_RULE_INVALID")
    if rule.requires_top_n and (rule.top_n is None or rule.top_n < 1):
        raise _conflict(f"Próg {rule.mode} wymaga dodatniego top_n.", "QUALIFICATION_RULE_INVALID")
    return rule


def _top_n_cutoff(totals: list[int], top_n: int) -> int | None:
    """Najniższa suma mieszcząca się w pierwszej ``top_n`` – albo ``None``, gdy nie ma kandydatów.

    Remis na granicy rozstrzyga się na korzyść uczestników: progiem jest *wartość* zajmująca
    miejsce ``top_n``, więc wszyscy z takim samym wynikiem wchodzą, choćby było ich więcej niż N.

    Zera nie biorą udziału w progu. „N najlepszych” w etapie, w którym zgłosiło się mniej osób niż
    N (albo w którym nikt nic nie ugrał), nie może oznaczać awansu za brak rozwiązania – kandydatem
    jest ten, kto zdobył choć punkt. Dlatego zwracany próg jest zawsze dodatni albo ``None``.
    """
    scoring = sorted((total for total in totals if total > 0), reverse=True)
    if not scoring:
        return None
    return scoring[min(top_n, len(scoring)) - 1]


def _qualified_entry_ids(rows: list[dict], rule) -> set[int]:
    """Identyfikatory wpisów spełniających próg. ``rows`` są już bez zdyskwalifikowanych."""
    mode = rule.mode
    if mode == QualificationMode.MIN_POINTS:
        return {row["entry_id"] for row in rows if row["total"] >= rule.min_points}
    if mode == QualificationMode.TOP_N:
        cutoff = _top_n_cutoff([row["total"] for row in rows], rule.top_n)
        if cutoff is None:
            return set()
        return {row["entry_id"] for row in rows if row["total"] >= cutoff}
    if mode == QualificationMode.TOP_N_PER_DISTRICT:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(_district_key(row["district"]), []).append(row)
        qualified: set[int] = set()
        for group in groups.values():
            cutoff = _top_n_cutoff([row["total"] for row in group], rule.top_n)
            if cutoff is None:
                continue
            qualified |= {row["entry_id"] for row in group if row["total"] >= cutoff}
        return qualified
    if mode == QualificationMode.HYBRID:
        cutoff = _top_n_cutoff([row["total"] for row in rows], rule.top_n)
        if cutoff is None:
            return set()
        return {row["entry_id"] for row in rows if row["total"] >= cutoff and row["total"] >= rule.min_points}
    raise _conflict(f"Nieznany tryb progu kwalifikacji: {mode}.", "QUALIFICATION_RULE_INVALID")


def qualified_with_manual(row: dict, rule_says: bool) -> bool:
    """Ostateczna odpowiedź „czy się kwalifikuje”: decyzja komitetu bije próg punktowy.

    Jedno miejsce dla obu czytelników – przeliczenia (``apply_qualification``) i symulacji
    (``apps.results.simulation``) – bo rozjazd między nimi znaczyłby, że ekran, na którym
    koordynator dobiera próg, pokazuje inny wynik niż późniejsze ogłoszenie.

    Pusta decyzja (najczęstszy przypadek) oddaje wynik reguły bez zmiany, więc dopisanie tej
    funkcji nie zmienia zachowania żadnego etapu, w którym komitet niczego nie rozstrzygał.
    """
    decision = row.get("manual_qualification") or ManualQualification.NONE
    if decision == ManualQualification.QUALIFIED:
        return True
    if decision == ManualQualification.NOT_QUALIFIED:
        return False
    return rule_says


def _district_key(value: str | None) -> str:
    """Okręgi grupujemy po znormalizowanej nazwie – „Mazowiecki” i „mazowiecki” to jeden okręg."""
    return (value or "").strip().casefold()


# --- kwalifikacja z danych: reguły przejścia (§ 1.2.5) -----------------------------------------
#
# Cały ten blok jest **martwy**, dopóki konkurs ma flagę ``process_editor`` wyłączoną: wchodzi się
# do niego wyłącznie przez ``stage_qualification``, a ta pyta o flagę raz, na wejściu. Arytmetyka
# nie jest tu przepisana ani o linijkę – próg liczy ta sama ``_top_n_cutoff``, a grupy dzieli ten
# sam ``_district_key``, co dzisiejsze ``_qualified_entry_ids``. Dzięki temu trzy zachowania
# brzegowe są wspólne z definicji, a nie przez zbieżność dwóch niezależnych implementacji:
# **zero nie kwalifikuje** w trybach z ``top_n``, **remis na progu wpuszcza wszystkich** (progiem
# jest wartość, nie miejsce) i **decyzja komitetu bije regułę** – także sumę reguł, bo dokłada ją
# ``qualified_with_manual`` już po zsumowaniu zbiorów.


def transition_rules_for(stage: Stage) -> list:
    """Reguły przejścia kroku tego etapu, w kolejności ``position``. Brak kroku = pusta lista.

    Odczyt idzie przez odwrotny akcesor (``pipeline_step``), a nie przez zapytanie po edycji: krok
    jest z etapem w relacji jeden-do-jednego, więc pytanie „jaki jest krok tego etapu” ma jedną
    odpowiedź i nie ma czego sortować. Pusta lista jest tu zwykłym stanem (krok, którego edytor
    procesu jeszcze nie dotknął), a nie błędem – co z nią zrobić, rozstrzyga
    :func:`stage_qualification`.
    """
    step = getattr(stage, "pipeline_step", None)
    if step is None:
        return []
    return list(step.transition_rules.all())


def _assert_transition_rule_valid(rule) -> None:
    """Komplet parametrów trybu – te same warunki i ten sam kod błędu, co w ``_rule_for``.

    Sprawdzenie jest powtórzone przy **odczycie**, mimo że model ma je w ``clean()``: reguła bywa
    wpisana migracją albo poprawiona w ``/admin/``, czyli drogą, która ``full_clean()`` omija.
    Próg bez liczby nie jest progiem łagodnym, tylko progiem, którego nie da się policzyć – i lepiej
    powiedzieć to przed przeliczeniem niż zakwalifikować kogoś przypadkiem.
    """
    if rule.requires_min_points and rule.min_points is None:
        raise _conflict(f"Reguła przejścia {rule.mode} wymaga min_points.", "QUALIFICATION_RULE_INVALID")
    if rule.requires_top_n and (rule.top_n is None or rule.top_n < 1):
        raise _conflict(
            f"Reguła przejścia {rule.mode} wymaga dodatniego top_n.", "QUALIFICATION_RULE_INVALID"
        )
    if rule.requires_percentile and (rule.percentile is None or not 1 <= rule.percentile <= 100):
        raise _conflict(
            f"Reguła przejścia {rule.mode} wymaga percentyla z zakresu 1–100.",
            "QUALIFICATION_RULE_INVALID",
        )


def _category_ids(rows: list[dict]) -> dict[int, int | None]:
    """Kategoria każdego wpisu: ``{entry_id: category_id}``.

    Wartość bierzemy z wiersza, jeżeli tam stoi, a dla reszty robimy **jedno** zapytanie na cały
    etap. Dwa źródła, bo wiersz niesie kategorię tylko w konkursie, który ma włączone kategorie
    (§ 1.2.4) – a reguła przejścia potrafi się do kategorii odwołać także wtedy, gdy tabela
    wyników jej nie pokazuje. Zapytania nie ma tam, gdzie nie ma po co: woła to wyłącznie reguła
    dzieląca albo zawężająca po kategorii, więc Konkurs #1 nie płaci za nie ani razu.
    """
    known = {row["entry_id"]: row.get("category_id") for row in rows if "category_id" in row}
    missing = [row["entry_id"] for row in rows if "category_id" not in row]
    if missing:
        known.update(dict(StageEntry.objects.filter(pk__in=missing).values_list("pk", "category_id")))
    return known


def _rule_rows(rows: list[dict], rule, categories: dict[int, int | None]) -> list[dict]:
    """Wiersze, których reguła dotyczy: wszystkie albo wyłącznie jedna kategoria."""
    if rule.category_id is None:
        return rows
    return [row for row in rows if categories.get(row["entry_id"]) == rule.category_id]


def _rule_groups(rows: list[dict], group_by: str, categories: dict[int, int | None]) -> list[list[dict]]:
    """Pole podzielone na grupy reguły. Bez podziału – jedna grupa ze wszystkimi wierszami.

    Grupowanie po regionie jest **tym samym** grupowaniem, co w dzisiejszym
    ``TOP_N_PER_DISTRICT``: po znormalizowanej etykiecie województwa (``_district_key``). „N na
    województwo” nie jest osobną arytmetyką, tylko szczególnym przypadkiem „N w grupie”.
    """
    if not group_by:
        return [rows]
    groups: dict[object, list[dict]] = {}
    for row in rows:
        if group_by == TransitionGroupBy.REGION:
            key: object = _district_key(row["district"])
        else:
            key = categories.get(row["entry_id"])
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def _percentile_top_n(count: int, percentile: int) -> int:
    """Ilu uczestników grupy to ``percentile`` procent pola – zaokrąglone **w górę**.

    W górę, bo „najlepsze 10 %” z pola siedmiu osób ma znaczyć jedną osobę, a nie zero: reguła
    zapisana w regulaminie jest obietnicą, że ktoś przejdzie. Rachunek jest całkowitoliczbowy
    (``-(-a // b)``), bo zaokrąglenie po drodze przez ``float`` potrafi dać przy setce wyników
    liczbę zależną od kolejności dodawania – ta sama decyzja, co przy wagach jako ułamkach
    zwykłych (§ 1.2.6). Samo odcięcie robi potem ``_top_n_cutoff``, więc zera nadal nie
    kwalifikują, a remis na progu nadal wpuszcza wszystkich.
    """
    return -(-count * percentile // 100)


def _qualified_by_rule(rows: list[dict], rule, categories: dict[int, int | None]) -> set[int]:
    """Wpisy spełniające **jedną** regułę przejścia.

    Odwzorowanie czterech dzisiejszych trybów jest jeden do jednego (§ 1.2.5): ``MIN_POINTS``
    liczy to samo porównanie, ``TOP_N`` i ``HYBRID`` to jedna grupa ze wszystkimi, a
    ``TOP_N_PER_GROUP`` z podziałem ``REGION`` to dzisiejsze ``TOP_N_PER_DISTRICT``.

    ``MANUAL`` nie kwalifikuje nikogo i to nie jest brak implementacji: tryb znaczy „przechodzi
    wyłącznie ten, komu komitet wpisał decyzję ręcznie”, a tę dokłada ``qualified_with_manual``
    poza tą funkcją – tak samo, jak dokłada ją do każdego innego trybu.
    """
    mode = rule.mode
    if mode == TransitionMode.MANUAL:
        return set()
    scope = _rule_rows(rows, rule, categories)
    if mode == TransitionMode.MIN_POINTS:
        return {row["entry_id"] for row in scope if row["total"] >= rule.min_points}
    qualified: set[int] = set()
    for group in _rule_groups(scope, rule.group_by, categories):
        totals = [row["total"] for row in group]
        if mode == TransitionMode.PERCENTILE:
            top_n = _percentile_top_n(len(totals), rule.percentile)
        else:
            top_n = rule.top_n
        if not top_n:
            continue
        cutoff = _top_n_cutoff(totals, top_n)
        if cutoff is None:
            continue
        for row in group:
            if row["total"] < cutoff:
                continue
            if mode == TransitionMode.HYBRID and row["total"] < rule.min_points:
                continue
            qualified.add(row["entry_id"])
    return qualified


def _qualified_by_transition_rules(rows: list[dict], rules, categories: dict[int, int | None]) -> set[int]:
    """**Suma** zbiorów wszystkich reguł kroku – jedyna dozwolona kompozycja (§ 1.2.5).

    Organizator pisze w regulaminie „do finału przechodzi 30 najlepszych **oraz** każdy, kto
    zdobył co najmniej 90 punktów” i to jest suma. Iloczyn ma własny tryb (``HYBRID``), bo „oraz”
    w regulaminie bywa jednym i drugim, a obie operacje wyrażone tą samą listą byłyby nieczytelne.
    """
    qualified: set[int] = set()
    for rule in rules:
        qualified |= _qualified_by_rule(rows, rule, categories)
    return qualified


@dataclass(frozen=True)
class StageQualification:
    """Próg etapu gotowy do zastosowania – **jedna** odpowiedź dla obu dróg.

    Obiekt istnieje po to, żeby pytanie o flagę padło raz (przy budowie), a nie przy każdym
    wierszu i nie drugi raz w symulacji. Niesie też ``mode`` – napis do audytu i do logu, a nie
    do tabeli wyników: dla drogi dzisiejszej jest to tryb ``QualificationRule``, dla drogi z danych
    tryby reguł kroku połączone znakiem ``+`` (bo reguł bywa kilka i każda jest osobną decyzją
    organizatora).
    """

    mode: str
    #: ``QualificationRule`` dla drogi dzisiejszej, ``None`` dla drogi z ``TransitionRule``.
    rule: object | None
    #: Reguły przejścia kroku; pusta krotka na drodze dzisiejszej.
    rules: tuple
    from_pipeline: bool

    def qualified_entry_ids(self, rows: list[dict]) -> set[int]:
        """Identyfikatory wpisów spełniających próg. ``rows`` są już bez zdyskwalifikowanych."""
        if not self.from_pipeline:
            return _qualified_entry_ids(rows, self.rule)
        categories = _category_ids(rows) if self.needs_categories else {}
        return _qualified_by_transition_rules(rows, self.rules, categories)

    @property
    def needs_categories(self) -> bool:
        """Czy któraś reguła w ogóle pyta o kategorię – od tego zależy jedno dodatkowe zapytanie."""
        return any(
            rule.category_id is not None or rule.group_by == TransitionGroupBy.CATEGORY for rule in self.rules
        )


def stage_qualification(stage: Stage) -> StageQualification:
    """Próg etapu: z ``TransitionRule`` przy włączonej fladze, z ``QualificationRule`` bez niej.

    Jedyne miejsce, w którym ta decyzja zapada, i jedyne wejście do progu dla przeliczenia
    i symulacji – rozjazd między nimi znaczyłby, że ekran, na którym koordynator dobiera próg,
    pokazuje inny wynik niż późniejsze ogłoszenie.

    **Odwrót na dzisiejszą regułę jest świadomy i jest tu najważniejszym zdaniem.** Przy włączonej
    fladze krok bez ani jednej reguły przejścia (albo etap, dla którego kroku w ogóle nie ma)
    czyta próg tam, gdzie czytał go zawsze – w ``QualificationRule``. Powód jest jeden i wynika
    z § 0.1: flaga mówi „edytor procesu jest dostępny”, a nie „każdy etap został już przez ten
    edytor przepisany”. Bez odwrotu włączenie flagi w trakcie sezonu zabierałoby próg etapom,
    których nikt jeszcze nie tknął – czyli zmieniałoby wynik, którego zmieniać nie wolno.
    Odwrotnością „nikt nie przechodzi” nie jest więc pusta lista reguł, tylko reguła w trybie
    ``MANUAL``; pustka znaczy „nie skonfigurowano”, a nie „nie kwalifikujemy”.
    """
    if process_editor_enabled(competition_of(stage)):
        rules = transition_rules_for(stage)
        if rules:
            for rule in rules:
                _assert_transition_rule_valid(rule)
            return StageQualification(
                # ``dict.fromkeys`` zamiast ``set``: napis w audycie ma być powtarzalny, a zbiór
                # nie ma kolejności. Kolejność jest ta, w której reguły stoją na ekranie.
                mode="+".join(dict.fromkeys(rule.mode for rule in rules)),
                rule=None,
                rules=tuple(rules),
                from_pipeline=True,
            )
    rule = _rule_for(stage)
    return StageQualification(mode=rule.mode, rule=rule, rules=(), from_pipeline=False)


def _next_stage_by_kind(stage: Stage) -> Stage | None:
    """Następny etap według stałej ``STAGE_ORDER`` – dzisiejsze ciało, przeniesione bez zmiany.

    Etap treningowy nie ma następnego – i nie jest następnym dla żadnego etapu, bo nie ma go
    w ``STAGE_ORDER``. Sprawdzenie jest jawne, a nie oparte na wyjątku z ``.index()``: „trening
    nie kwalifikuje” to reguła, którą trzeba przeczytać w kodzie, a nie wywnioskować z braku.
    """
    if stage.is_training:
        return None
    try:
        index = STAGE_ORDER.index(stage.kind)
    except ValueError:  # pragma: no cover - kind pochodzi z choices
        return None
    for kind in STAGE_ORDER[index + 1 :]:
        found = Stage.objects.filter(edition_id=stage.edition_id, kind=kind).first()
        if found is not None:
            return found
    return None


def next_stage_of(stage: Stage) -> Stage | None:
    """Następny etap tej samej edycji. Ostatni etap toru nie ma następnego.

    Przy wyłączonej fladze ``process_editor`` odpowiedź pochodzi z ``STAGE_ORDER`` – tej samej
    krotki i tą samą drogą, co przed etapem 2 (ELIM → DISTRICT → FINAL). Przy włączonej –
    z ``PipelineStep.position``. Obie odpowiedzi muszą być dla Konkursu #1 **równe** i to jest
    osobny test (§ 5.2, ``test_pipeline_matches_stage_order``).

    Krok ``off_pipeline`` (trening, warsztat, sesja próbna) nie ma następnika i nie jest niczyim
    następnikiem – dokładnie to, co dziś znaczy nieobecność w ``STAGE_ORDER``. Etap bez kroku przy
    włączonej fladze też nie ma następnika: skoro przebieg jest danymi, to etap spoza przebiegu nie
    ma miejsca, z którego można by pójść dalej. To jest jedyne miejsce, w którym odwrotu na
    ``STAGE_ORDER`` **nie** ma – i nie może być, bo konkurs o pięciu rundach ma wszystkie etapy
    rodzaju ``ROUND``, a krotka nie umie ich ustawić w kolejności.
    """
    if not process_editor_enabled(competition_of(stage)):
        return _next_stage_by_kind(stage)
    step = getattr(stage, "pipeline_step", None)
    if step is None or step.off_pipeline:
        return None
    following = (
        PipelineStep.objects.filter(
            edition_id=stage.edition_id, off_pipeline=False, position__gt=step.position
        )
        .select_related("stage")
        .order_by("position", "id")
        .first()
    )
    return following.stage if following is not None else None


def _sync_next_stage(following: Stage, candidates: list[dict]) -> tuple[int, int, list[str]]:
    """Dopasowuje wpisy w następnym etapie do wyniku kwalifikacji.

    Zwraca ``(utworzone, usunięte, konflikty)``. Trzy reguły:

    - zakwalifikowani dostają wpis ``REGISTERED`` – jednym ``bulk_create(ignore_conflicts=True)``,
      więc powtórne wywołanie nic nie duplikuje i nie kosztuje zapytania na uczestnika,
    - **odkwalifikowani tracą wpis**, ale tylko jeśli jest jeszcze ``REGISTERED`` i pusty. Ponowne
      przeliczenie (np. po decyzji reklamacyjnej, która komuś odebrała punkty) nie może zostawić
      w następnym etapie ludzi, którzy się do niego nie kwalifikują,
    - wpis z choćby jednym zgłoszeniem **albo z zapisem na rozmowę** zostaje i trafia na listę
      konfliktów. Skasowanie go usunęłoby pracę, którą ktoś naprawdę oddał, albo termin, na który
      ktoś dostał potwierdzenie mailem; to decyzja dla koordynatora, nie dla serwisu. Zapis na
      rozmowę liczy się tu tak samo jak praca, bo tak samo jest zobowiązaniem wobec uczestnika –
      a przy ``InterviewBooking.slot`` z ``PROTECT`` kasowanie wpisu i tak skończyłoby się
      ``ProtectedError`` w środku przeliczenia.
    """
    qualified = [row for row in candidates if row["qualified"]]
    demoted = {row["participant_id"]: row for row in candidates if not row["qualified"]}

    existing = set(
        StageEntry.objects.filter(
            stage=following, participant_id__in=[row["participant_id"] for row in candidates]
        ).values_list("participant_id", flat=True)
    )
    missing = [row for row in qualified if row["participant_id"] not in existing]
    if missing:
        StageEntry.objects.bulk_create(
            [
                StageEntry(
                    participant_id=row["participant_id"],
                    stage=following,
                    status=StageEntryStatus.REGISTERED,
                )
                for row in missing
            ],
            ignore_conflicts=True,
        )

    stale_ids: list[int] = []
    conflicts: list[str] = []
    if demoted:
        stale = (
            StageEntry.objects.filter(
                stage=following,
                participant_id__in=list(demoted),
                status=StageEntryStatus.REGISTERED,
            )
            # ``distinct=True`` przy dwóch licznikach naraz: bez tego złączenie zgłoszeń mnożyłoby
            # wiersze zapisu na rozmowę (i odwrotnie), a liczniki wyszłyby jako iloczyn.
            .annotate(
                submission_count=Count("submissions", distinct=True),
                booking_count=Count("interview_booking", distinct=True),
            )
            .only("id", "participant_id")
        )
        for entry in stale:
            if entry.submission_count or entry.booking_count:
                conflicts.append(demoted[entry.participant_id]["public_code"])
            else:
                stale_ids.append(entry.pk)
    if stale_ids:
        StageEntry.objects.filter(pk__in=stale_ids).delete()
    return len(missing), len(stale_ids), sorted(conflicts)


@transaction.atomic
def apply_qualification(stage: Stage, *, actor=None, request=None) -> dict:
    """Przelicza wyniki i ustawia statusy kwalifikacji, tworząc wpisy w następnym etapie.

    Wymaga zamkniętego okna reklamacji (409 ``APPEAL_WINDOW_OPEN``) i blokuje wiersz etapu na czas
    transakcji, żeby dwa równoległe przeliczenia nie deptały sobie po statusach.

    ``DISQUALIFIED`` zostaje nietknięty i nie bierze udziału w progu – dyskwalifikacja jest
    decyzją proceduralną, a nie wynikiem punktowym, więc nie może zajmować miejsca w „top N”.
    Idempotentne i **odwracalne**: drugie wywołanie niczego nie duplikuje, a jeśli ktoś stracił
    kwalifikację, sprząta po nim pusty wpis w następnym etapie (``_sync_next_stage``).
    """
    stage = _locked_stage(stage)
    _assert_appeal_window_closed(stage)
    # Próg rozstrzygamy **przed** przeliczeniem, tak jak dotąd: etap bez progu ma odmówić, zanim
    # policzy tabelę, której i tak nie ma jak zastosować. ``stage_qualification`` jest jedynym
    # miejscem, w którym pada pytanie o flagę ``process_editor`` (§ 1.0 (c)).
    qualification = stage_qualification(stage)
    rows = compute_stage_results(stage)

    candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    qualified_ids = qualification.qualified_entry_ids(candidates)

    entries = {
        entry.pk: entry
        for entry in StageEntry.objects.filter(pk__in=[row["entry_id"] for row in candidates]).select_related(
            "participant"
        )
    }
    changed: list[StageEntry] = []
    for row in candidates:
        entry = entries[row["entry_id"]]
        row["qualified"] = qualified_with_manual(row, entry.pk in qualified_ids)
        target = StageEntryStatus.QUALIFIED if row["qualified"] else StageEntryStatus.NOT_QUALIFIED
        if entry.status != target:
            entry.status = target
            changed.append(entry)
        row["status"] = target
    for row in rows:
        # Zdyskwalifikowany nie kwalifikuje się nigdy – w tabeli musi mieć jawne ``False``.
        row.setdefault("qualified", False)
    if changed:
        StageEntry.objects.bulk_update(changed, ["status"])

    following = next_stage_of(stage)
    created_entries = removed_entries = 0
    conflicts: list[str] = []
    if following is not None:
        created_entries, removed_entries, conflicts = _sync_next_stage(following, candidates)

    summary = {
        "stage_id": stage.pk,
        "mode": qualification.mode,
        "qualified": sum(1 for row in candidates if row["qualified"]),
        "not_qualified": sum(1 for row in candidates if not row["qualified"]),
        "disqualified": len(rows) - len(candidates),
        "next_stage_id": following.pk if following is not None else None,
        "created_entries": created_entries,
        "removed_entries": removed_entries,
        # Pseudonimy wpisów, których nie wolno było skasować – do decyzji koordynatora.
        "next_stage_conflicts": conflicts,
        "rows": rows,
    }
    audit(
        actor,
        "results.qualification_applied",
        stage,
        # Do audytu idą wyłącznie liczniki: ani tabela z nazwiskami, ani lista pseudonimów.
        {key: value for key, value in summary.items() if key not in ("rows", "next_stage_conflicts")}
        | {"next_stage_conflicts": len(conflicts)},
        request=request,
    )
    logger.info(
        "Etap %s: kwalifikacja %s – %s zakwalifikowanych, %s nowych i %s usuniętych wpisów "
        "w etapie %s (%s konfliktów)",
        stage.pk,
        qualification.mode,
        summary["qualified"],
        created_entries,
        removed_entries,
        summary["next_stage_id"],
        len(conflicts),
    )
    return summary


# --- publikacja -------------------------------------------------------------------------------


def _initials(first_name: str, last_name: str) -> str:
    parts = [part.strip()[:1].upper() for part in (first_name, last_name) if part and part.strip()]
    return "".join(f"{letter}." for letter in parts)


def _school_key(value: str | None) -> str:
    """Szkoły grupujemy po znormalizowanej nazwie – „XIV LO” i „xiv lo” to jedna szkoła."""
    return (value or "").strip().casefold()


def _may_show_full_name(row: dict) -> bool:
    """Czy wolno podpisać ten wiersz imieniem i nazwiskiem (PROJEKT.md 2.4).

    Trzy warunki naraz, wszystkie muszą być spełnione:

    - **laureat** – nazwisko publikujemy tylko przy wyniku, który jest wyróżnieniem
      (``qualified``); przegranych finalistów tabela wymienia pod pseudonimem,
    - **zgoda uczestnika** (``publish_full_name``),
    - **zgoda opiekuna** dla niepełnoletniego – małoletni nie udziela jej sam skutecznie.
    """
    if not row.get("qualified") or not row.get("publish_full_name"):
        return False
    return bool(row.get("guardian_consent") or row.get("is_adult"))


def _display_name(row: dict, anonymization: str, school_sizes: dict[str, int]) -> str:
    """Jedyne miejsce, w którym powstaje etykieta uczestnika w publikowanej tabeli.

    Reguła domyślnie zamknięta: każdy tryb, który nie ma kompletu danych albo zgód, spada do
    pseudonimu. ``FULL`` przepuszcza tylko wiersze z ``_may_show_full_name`` (a sam tryb jest
    dopuszczony wyłącznie w finale – patrz ``publish_results``). ``INITIALS_SCHOOL`` wymaga do tego
    grupy co najmniej ``MIN_SCHOOL_GROUP`` uczestników z tej szkoły w tym etapie: „J.K., XIV LO”
    przy jednym uczestniku z XIV LO to nie anonimizacja, tylko wskazanie palcem.

    **Wpis drużynowy** (§ 1.2.3) podpisuje się nazwą drużyny – w tabeli konkursu drużynowego stoi
    skład, a nie osoba, i to jego nazwę zna regulamin. Reguły zgód nie stosujemy do niego wcale,
    bo nie ma czego: nazwa drużyny nie jest niczyim nazwiskiem, a zgodę na publikację składa się
    za siebie. Tryb ``CODE`` zostaje przy kodzie publicznym także dla drużyny i to jest decyzja,
    nie przeoczenie: tabela po pseudonimach ma być po pseudonimach bez wyjątku, a nazwę składu
    bywa, że da się przypisać osobom (skład bywa dwuosobowy i podpisany nazwiskami).
    """
    code = row["public_code"]
    if row.get("team_name"):
        return code if anonymization == Anonymization.CODE else (row["team_name"] or code)
    if anonymization == Anonymization.FULL:
        if not _may_show_full_name(row):
            return code
        full = " ".join(part for part in (row["first_name"], row["last_name"]) if part).strip()
        return full or code
    if anonymization == Anonymization.INITIALS_SCHOOL:
        initials = _initials(row["first_name"], row["last_name"])
        school = (row.get("school") or "").strip()
        if not initials or not school:
            return code
        if school_sizes.get(_school_key(school), 0) < MIN_SCHOOL_GROUP:
            return code
        return f"{initials}, {school}"
    return code


def build_snapshot(rows: list[dict], anonymization: str) -> list[dict]:
    """Zamrożona tabela: ``rank``, ``display``, ``points``, ``total``, ``qualified``, ``manual``
    i – wyłącznie przy ``CODE`` – ``district``.

    Kształt jest budowany od zera z jawnie wypisanych pól, a nie przez usuwanie kluczy z wiersza
    roboczego – dopisanie kiedyś kolumny z danymi osobowymi do ``compute_stage_results`` nie może
    w żaden sposób „przeciec” do publikacji.

    Okręg zostaje tylko w tabeli po pseudonimach: tam jest jedyną informacją o kontekście i niczego
    nie zawęża. Doklejony do inicjałów ze szkołą albo do nazwiska nie dodaje nic, czego czytelnik już
    nie wie, a mnoży cechy quasi-identyfikujące (PROJEKT.md 2.4).

    **Kategoria (§ 1.2.4) dochodzi wyłącznie wtedy, gdy niesie ją wiersz** – czyli wyłącznie
    w konkursie z włączoną flagą ``categories``. Snapshot Konkursu #1 nie zyskuje ani jednego
    klucza i to jest sprawdzane porównaniem słowników **na równość**, a nie na podzbiór (§ 5.2).
    Kategoria nie jest przy tym cechą quasi-identyfikującą w rozumieniu okręgu: jest nią grupa
    startowa ogłoszona w regulaminie, w której tabela i tak jest publikowana osobno – bez niej
    czytelnik nie wie, czyje miejsce „1” właśnie czyta.
    """
    school_sizes = Counter(_school_key(row.get("school")) for row in rows)
    snapshot = []
    for row in rows:
        item = {
            "rank": row["rank"],
            "display": _display_name(row, anonymization, school_sizes),
            # Punkty i suma idą do JSON-a przez ``points_json``: liczba całkowita zostaje ``int``
            # (snapshot etapu „tylko ze skali” jest co do bajtu taki, jak przed wydaniem 0.35.0),
            # a ułamkowa staje się liczbą JSON z najwyżej dwoma miejscami (``4.25``).
            "points": {number: points_json(value) for number, value in row["points"].items()},
            "total": points_json(row["total"]),
            "qualified": bool(row.get("qualified")),
            # Czy o tym wierszu rozstrzygnęła decyzja komitetu, a nie próg. Sama flaga, bez
            # uzasadnienia i bez rodzaju decyzji: ogłoszona tabela ma powiedzieć, że wynik nie
            # wynika z punktów (inaczej wygląda na błąd rachunkowy), a nie opowiedzieć, co się
            # przydarzyło konkretnemu uczestnikowi.
            "manual": bool(row.get("manual_qualification")),
        }
        if "category" in row:
            item["category"] = row["category"]
        if anonymization == Anonymization.CODE:
            item["district"] = row["district"]
        snapshot.append(item)
    return snapshot


@transaction.atomic
def publish_results(stage: Stage, actor, anonymization: str, *, request=None) -> ResultsPublication:
    """Publikuje wyniki etapu: przelicza, kwalifikuje i zamraża zanonimizowaną tabelę.

    Ponowna publikacja nadpisuje snapshot tego samego rekordu (jeden etap = jedna tabela w mocy)
    i zostawia wpis w audycie. ``diff`` audytu ma wyłącznie liczniki – tabela wyników z nazwiskami
    nie może wylądować w logu czytanym przez osoby bez prawa do danych osobowych.

    Bramki wejściowe: znany tryb anonimizacji, ``FULL`` wyłącznie w finale i zamknięte okno
    reklamacji. Każda z nich wypada przed zapisem, więc odrzucona publikacja nie zostawia śladu.
    """
    if anonymization not in Anonymization.values:
        raise _bad_request(f"Nieznany tryb anonimizacji: {anonymization}.", "INVALID_ANONYMIZATION")
    stage = _locked_stage(stage)
    if anonymization == Anonymization.FULL and stage.kind != StageKind.FINAL:
        # Nazwiska publikuje się przy laureatach finału i nigdzie indziej: tabela eliminacji
        # z nazwiskami to lista kilkunastu tysięcy uczniów wraz z ich porażkami (PROJEKT.md 2.4).
        raise _bad_request(
            "Pełne nazwiska wolno publikować wyłącznie w wynikach finału.",
            "ANONYMIZATION_NOT_ALLOWED_FOR_STAGE",
        )
    _assert_appeal_window_closed(stage)

    summary = apply_qualification(stage, actor=actor, request=request)
    snapshot = build_snapshot(summary["rows"], anonymization)
    now = timezone.now()

    publication, created = ResultsPublication.objects.update_or_create(
        stage=stage,
        defaults={
            "published_at": now,
            "published_by": actor if getattr(actor, "is_authenticated", False) else None,
            "anonymization": anonymization,
            "snapshot": snapshot,
            # Klucz do „mojego wyniku” w ogłoszonej tabeli. Wierszy snapshotu nie da się przypisać
            # do osoby (i dobrze), a uczestnik musi wiedzieć, z czym porównać swoje bieżące punkty.
            "entry_totals": {str(row["entry_id"]): points_json(row["total"]) for row in summary["rows"]},
        },
    )
    stage.results_published_at = now
    stage.save(update_fields=["results_published_at"])
    # Powiadomienie uczestników. Treść i krąg odbiorców należą do ``submissions.notifications``
    # (jedno miejsce na całą pocztę do uczestnika); listy idą po commicie, więc wycofana
    # publikacja nie ogłasza tabeli, której nie ma.
    publication.stage = stage
    notify_results_published(publication, request=request)

    audit(
        actor,
        "results.published",
        publication,
        {
            "stage_id": stage.pk,
            "anonymization": anonymization,
            "rows": len(snapshot),
            "qualified": summary["qualified"],
            "republished": not created,
        },
        request=request,
    )
    logger.info(
        "Etap %s: opublikowano wyniki (%s wierszy, tryb %s, ponowna publikacja: %s)",
        stage.pk,
        len(snapshot),
        anonymization,
        not created,
    )
    # Zgłoszenie zdarzenia systemom zewnętrznym (``apps.integrations``). Wiersze doręczeń powstają
    # w tej transakcji, a samo wysłanie idzie na kolejkę po commicie – wycofana publikacja nie
    # ogłasza tabeli, której nie ma, a awaria serwera partnera nie przewraca „Opublikuj wyniki”.
    from apps.integrations.events import results_published

    results_published(publication, rows=len(snapshot))
    return publication


# --- odczyt dla API ---------------------------------------------------------------------------


def published_results(stage_id: int) -> ResultsPublication | None:
    """Publikacja etapu albo ``None``. Publiczny widok nie dotyka poza tym żadnej innej tabeli."""
    return ResultsPublication.objects.filter(stage_id=stage_id).select_related("stage").first()


def _feedback_for(submission: Submission | None) -> list[dict]:
    """Informacja zwrotna dla uczestnika: komentarz i adnotacje **publiczne**.

    Nigdy ``comment_internal`` i nigdy tożsamości recenzenta (PROJEKT.md 2.4). Recenzje bez treści
    dla uczestnika są pomijane – pusta pozycja tylko zdradzałaby liczbę recenzentów.
    """
    if submission is None:
        return []
    feedback = []
    for review in submission.reviews.all():
        if review.status != ReviewStatus.SUBMITTED:
            continue
        comment = (review.comment_for_participant or "").strip()
        annotations = review.public_annotations()
        if not comment and not annotations:
            continue
        feedback.append({"comment_for_participant": comment, "annotations": annotations})
    return feedback


def results_for_participant(user, competition=None) -> list[dict]:
    """Własne wyniki uczestnika – wyłącznie z etapów, których wyniki są już opublikowane.

    Punkty liczymy **na żywo**, z aktualnych ``FinalGrade``: uczestnikowi należy się prawda o jego
    pracy, także wtedy, gdy komisja zmieniła ocenę po ogłoszeniu tabeli. Publiczna tabela zostaje
    przy tym zamrożona, więc oba widoki mogą się rozjechać – i wtedy wiersz niesie ``published_total``
    (suma z ogłoszonej tabeli) oraz ``differs_from_published=True``. Milczące pokazanie jednej
    z dwóch różnych liczb byłoby gorsze niż pokazanie obu (PROJEKT.md 2.4).

    Widoczność bez zmian: przed ``Stage.results_published_at`` etap w ogóle nie jest zwracany.

    ``competition`` wskazuje, **czyje** wyniki pokazujemy: uczeń startujący w dwóch olimpiadach ma
    pod każdą domeną zobaczyć wyniki tej jednej, bo profil, kod publiczny i tabela wyników są
    osobne dla każdego konkursu (§ 3.3).
    """
    from apps.accounts.services import participant_for

    participant = participant_for(user, competition)
    if participant is None:
        return []
    entries = list(
        StageEntry.objects.filter(participant=participant, stage__results_published_at__isnull=False)
        .select_related("stage", "stage__edition")
        .order_by("stage__opens_at", "stage_id")
    )
    if not entries:
        return []

    stage_ids = [entry.stage_id for entry in entries]
    published_totals: dict[int, dict] = {
        publication.stage_id: publication.entry_totals or {}
        for publication in ResultsPublication.objects.filter(stage_id__in=stage_ids).only(
            "stage_id", "entry_totals"
        )
    }
    problems: dict[int, list] = {stage_id: [] for stage_id in stage_ids}
    for problem in Problem.objects.filter(stage_id__in=stage_ids).order_by("number", "id"):
        problems[problem.stage_id].append(problem)

    submissions = (
        Submission.objects.filter(entry__in=entries)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("final_grade")
        .prefetch_related(Prefetch("reviews", queryset=Review.objects.order_by("round", "id")))
        .order_by("entry_id", "problem_id", "-version")
    )
    latest: dict[tuple[int, int], Submission] = {}
    for submission in submissions:
        latest.setdefault((submission.entry_id, submission.problem_id), submission)

    results = []
    for entry in entries:
        stage = entry.stage
        rows = []
        total = Decimal(0)
        for problem in problems.get(stage.pk, []):
            submission = latest.get((entry.pk, problem.pk))
            grade = getattr(submission, "final_grade", None) if submission is not None else None
            score = grade.score if grade is not None else Decimal(0)
            total += score
            rows.append(
                {
                    "problem_id": problem.pk,
                    "number": problem.number,
                    "title": problem.title,
                    "score": score,
                    "feedback": _feedback_for(submission),
                }
            )
        # Snapshot sprzed 0.35.0 niesie ``int``, nowszy – liczbę JSON (``int`` albo ``float``);
        # ``to_points`` czyta obie postaci do ``Decimal``, więc porównanie z sumą na żywo jest dokładne.
        published = to_points(published_totals.get(stage.pk, {}).get(str(entry.pk)))
        results.append(
            {
                "stage_id": stage.pk,
                # ``stage_kind`` zostaje surowym kodem (zgodność API), a podpis dla człowieka
                # idzie osobno: panel uczestnika wypisywał dotąd sam kod („DISTRICT”), a od
                # kiedy koordynator nadaje etapom nazwy, to ta nazwa ma tam stać.
                "stage_kind": stage.kind,
                "stage_name": stage.display_name,
                "edition": stage.edition.year_label,
                "results_published_at": stage.results_published_at,
                "status": entry.status,
                "qualified": entry.status == StageEntryStatus.QUALIFIED,
                "total_points": total,
                "published_total": published,
                "differs_from_published": published is not None and published != total,
                "problems": rows,
            }
        )
    return results
