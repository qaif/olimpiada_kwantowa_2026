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

from django.db import transaction
from django.db.models import Count, Prefetch
from django.utils import timezone
from rest_framework import status as http

from apps.competitions.models import (
    ManualQualification,
    Problem,
    QualificationMode,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageKind,
)
from apps.core.api import DomainError
from apps.core.models import audit
from apps.grading.models import Review, ReviewStatus
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.notifications import notify_results_published

from .models import Anonymization, ResultsPublication

logger = logging.getLogger(__name__)

#: Kolejność etapów edycji. Kwalifikacja przenosi uczestnika do następnego – finał nie ma następcy.
#: ``StageKind.TRAINING`` **nie ma** na tej liście i to jest cała reguła „trening jest poza
#: kwalifikacją”: etap treningowy nie ma następnego etapu i nigdy nie jest niczyim następnym.
STAGE_ORDER = (StageKind.ELIM, StageKind.DISTRICT, StageKind.FINAL)

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

#: Wiek, od którego uznajemy uczestnika za pełnoletniego przy zgodzie na publikację nazwiska.
#: 19, a nie 18: znamy wyłącznie rok urodzenia, więc konserwatywnie zaokrąglamy w stronę ochrony –
#: osoba, która skończy 18 lat w tym roku, wciąż wymaga zgody opiekuna.
ADULT_AGE = 19


# --- błędy domenowe ---------------------------------------------------------------------------


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


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


def _rank_rows(rows: list[dict]) -> list[dict]:
    """Nadaje miejsca: malejąco po sumie, remis = to samo miejsce (1, 1, 3).

    Porządek wewnątrz remisu jest po ``public_code``, żeby tabela była powtarzalna – dwie
    publikacje tych samych danych muszą dać ten sam plik.
    """
    ordered = sorted(rows, key=lambda row: (-row["total"], row["public_code"]))
    rank = 0
    previous_total: int | None = None
    for index, row in enumerate(ordered, start=1):
        if previous_total is None or row["total"] != previous_total:
            rank = index
            previous_total = row["total"]
        row["rank"] = rank
    return ordered


def _is_adult(birth_year: int | None, current_year: int) -> bool:
    """Czy uczestnik jest pełnoletni „na pewno”, licząc wyłącznie po roku urodzenia."""
    if not birth_year:
        return False
    return current_year - int(birth_year) >= ADULT_AGE


def _quiz_scores(stage: Stage, *, preview: bool) -> dict[int, int] | None:
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
    """
    problems = list(stage.problems.order_by("number", "id"))
    entries = list(
        StageEntry.objects.filter(stage=stage)
        .select_related("participant", "participant__user")
        .order_by("id")
    )
    latest = _latest_submissions(stage)
    quiz_scores = _quiz_scores(stage, preview=preview)
    # Pełnoletność liczymy raz na cały etap: znamy tylko rok urodzenia, więc dokładniejszej daty
    # i tak nie ma. Do wiersza trafia gotowa flaga, nigdy sam ``birth_year`` – rok urodzenia nie ma
    # po co wędrować przez warstwy aż do serializera.
    current_year = timezone.now().year

    pending_codes: list[str] = []
    rows: list[dict] = []
    for entry in entries:
        participant = entry.participant
        points: dict[str, int] = {}
        total = 0
        for problem in problems:
            submission = latest.get((entry.pk, problem.pk))
            score = 0
            if submission is not None:
                if _blocks_finalization(submission):
                    pending_codes.append(participant.public_code)
                grade = getattr(submission, "final_grade", None)
                score = int(grade.score) if grade is not None else 0
            points[str(problem.number)] = score
            total += score
        if quiz_scores is not None:
            # Etap w formie testu online nie ma zadań ani prac, więc pętla wyżej nic nie policzyła.
            # Suma przychodzi w całości z ``apps.quiz`` i **zastępuje** sumę z zadań, a nie dokłada
            # się do niej: gdyby etap miał jedno i drugie, byłby etapem o dwóch formach naraz –
            # a takiego stanu nie da się opisać ani w regulaminie, ani w tabeli wyników.
            # ``points`` zostaje pusty, bo kolumny tabeli wyników to zadania (``problem_numbers``),
            # a pytania testu są ich zbyt drobnym i zbyt licznym odpowiednikiem; rozbicie na
            # pytania stoi na własnym ekranie (``/coordinator/stages/<id>/quiz/results/``).
            total = quiz_scores.get(entry.pk, 0)
        rows.append(
            {
                "entry_id": entry.pk,
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
                "is_adult": _is_adult(participant.birth_year, current_year),
                "status": entry.status,
                # Decyzja komitetu o kwalifikacji wbrew progowi (pusta = rozstrzyga próg).
                # Wędruje w wierszu, bo czytają ją trzy różne warstwy: kwalifikacja
                # (``manual_qualified``), snapshot (odznaka w ogłoszonej tabeli) i symulacja.
                "manual_qualification": entry.manual_qualification,
                "points": points,
                "total": total,
            }
        )

    if preview:
        return _rank_rows(rows)

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
    return _rank_rows(rows)


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


def next_stage_of(stage: Stage) -> Stage | None:
    """Następny etap tej samej edycji (ELIM → DISTRICT → FINAL). Finał nie ma następnego.

    Etap treningowy też nie ma – i nie jest następnym dla żadnego etapu, bo nie ma go
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
    rule = _rule_for(stage)
    rows = compute_stage_results(stage)

    candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    qualified_ids = _qualified_entry_ids(candidates, rule)

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
        "mode": rule.mode,
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
        rule.mode,
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
    """
    code = row["public_code"]
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
    """
    school_sizes = Counter(_school_key(row.get("school")) for row in rows)
    snapshot = []
    for row in rows:
        item = {
            "rank": row["rank"],
            "display": _display_name(row, anonymization, school_sizes),
            "points": dict(row["points"]),
            "total": row["total"],
            "qualified": bool(row.get("qualified")),
            # Czy o tym wierszu rozstrzygnęła decyzja komitetu, a nie próg. Sama flaga, bez
            # uzasadnienia i bez rodzaju decyzji: ogłoszona tabela ma powiedzieć, że wynik nie
            # wynika z punktów (inaczej wygląda na błąd rachunkowy), a nie opowiedzieć, co się
            # przydarzyło konkretnemu uczestnikowi.
            "manual": bool(row.get("manual_qualification")),
        }
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
            "entry_totals": {str(row["entry_id"]): row["total"] for row in summary["rows"]},
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
        total = 0
        for problem in problems.get(stage.pk, []):
            submission = latest.get((entry.pk, problem.pk))
            grade = getattr(submission, "final_grade", None) if submission is not None else None
            score = int(grade.score) if grade is not None else 0
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
        published = published_totals.get(stage.pk, {}).get(str(entry.pk))
        published = int(published) if published is not None else None
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
