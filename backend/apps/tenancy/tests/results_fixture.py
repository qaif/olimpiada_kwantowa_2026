"""Edycja w kształcie Olimpiady Kwantowej – dane, na których liczy się snapshot tabel wyników.

Po co osobny moduł obok ``golden.py``: złota fikstura opisuje świat **w trakcie** sezonu (praca
oddana, praca w ocenianiu, brak pracy), bo taki świat renderują panele. Test snapshotu tabel
(``docs/UNIWERSALNY-ETAP-2.md`` § 5.2) potrzebuje natomiast świata **po** zamknięciu oceniania
i z rozstrzygnięciami, których w złotej fiksturze nie ma: remisem na progu, kompletem zer, wpisem
zdyskwalifikowanym i decyzją komitetu w obie strony. Dołożenie tego wszystkiego do ``build_golden``
zmieniłoby świat kilkudziesięciu testów złotych (razem z ich budżetami zapytań), więc kształt
„produkcja po ogłoszeniu wyników” stoi tutaj, a nie tam.

**Zero danych osobowych** – ta sama reguła, co w ``golden.py``: nazwiska są generowane, adresy
stoją w domenie ``example.invalid`` (RFC 2606), a szkoły są napisami. Fikstura opisuje kształt
(cztery etapy, cztery tryby progu, trzy województwa), nie treść.

**Kody publiczne są jawne, a nie losowane.** ``Participant.public_code`` jest domyślnie losowy
(``generate_public_code``), a tabela wyników sortuje się po nim i wypisuje go w każdym wierszu
snapshotu – fikstura z losowymi kodami dawałaby więc inną tabelę przy każdym przebiegu i nie dałoby
się jej zamrozić w pliku. Kody składamy z **prefiksu konkursu**, a nie z literału: prefiks ``OLM-``
jest zamrożony (§ 0.2 punkt 8) i pilnuje go osobny test, więc jego zmiana ma się tu odezwać.

Czego w tej fiksturze **nie ma**: kategorii, komponentów etapu, drużyn, wag zadań i przesunięcia
skali. Konkurs #1 nie ma żadnej z tych rzeczy, a test § 5.2 pyta o to, czy jego tabele są takie
same dwiema drogami – nie o to, jak liczą się tabele konkursu, którego jeszcze nie ma.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.apps import apps as django_apps
from django.utils import timezone

from apps.accounts.models import Participant, Voivodeship
from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import (
    TRAINING_DEADLINE,
    Edition,
    ManualQualification,
    QualificationMode,
    Stage,
    StageEntryStatus,
    StageFormat,
    StageKind,
)
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

#: Kroki przebiegu i reguły przejścia pisze **migracja**, a nie własna pętla tego modułu. Nazwa
#: modułu zaczyna się od cyfry, więc ``from … import …`` jest tu składniowo niemożliwe.
pipeline_from_stages = importlib.import_module("apps.competitions.migrations.0024_pipeline_from_stages")

#: Domena adresów – RFC 2606 gwarantuje, że nie istnieje i nie zacznie istnieć.
MAIL_DOMAIN = "example.invalid"

#: Etykieta edycji. Inna niż w ``golden.py``, bo więz ``competitions_edition_unique_year_label``
#: jest per konkurs, a oba światy bywają budowane na tym samym Konkursie #1.
EDITION_LABEL = "I edycja testowa (snapshot)"

#: Szkoła wspólna dla trzech uczestników – dokładnie próg k-anonimowości ``MIN_SCHOOL_GROUP``,
#: przy którym tryb „inicjały i szkoła” w ogóle wolno pokazać. Reszta ma szkoły własne, więc
#: w tym samym snapshocie widać **obie** gałęzie ``_display_name``.
SHARED_SCHOOL = "Liceum testowe nr 1"

#: Ile lat ma uczestnik „na pewno pełnoletni” i „na pewno niepełnoletni”. Liczone od bieżącego
#: roku, a nie wpisane literałem: ``_is_adult`` porównuje rok urodzenia z rokiem dzisiejszym, więc
#: rocznik z literału po kilku sezonach zacząłby opisywać kogoś innego – a zamrożona tabela
#: zmieniłaby się bez zmiany w kodzie.
ADULT_AGE_YEARS = 25
MINOR_AGE_YEARS = 16

#: Wiek etapu liczony wstecz od „teraz”. Okno reklamacji zamyka się 37 dni po otwarciu etapu
#: (fabryka: deadline +14 dni, recenzje +14, otwarcie okna +2, zamknięcie +7), a progi i publikacja
#: wolno liczyć dopiero po jego zamknięciu (PROJEKT.md 2.4) – stąd odstępy grubo ponad 37 dni.
STAGE_AGE_DAYS = {"elim": 150, "district": 120, "final": 90}


@dataclass
class ResultsWorld:
    """Świat fikstury – uchwyty do tego, o co pyta test, z nazwami z domeny, nie z ORM-a."""

    competition: object
    edition: Edition
    training: Stage
    elim: Stage
    district: Stage
    final: Stage
    participants: dict[str, Participant] = field(default_factory=dict)

    def stage(self, key: str) -> Stage:
        return getattr(self, key)


# --- ludzie ---------------------------------------------------------------------------------------
#
# Dziewięcioro uczestników i ani jednego więcej: każdy wnosi do tabeli inną **decyzję**, a nie inny
# wiersz. Kolumny są kolejno: kod, województwo, szkoła, pełnoletność, zgoda na publikację nazwiska
# i zgoda opiekuna – czyli dokładnie to, co czyta ``_display_name`` i ``_may_show_full_name``.

PEOPLE = (
    # Trójka z jednej szkoły – dopiero przy trzech wolno pokazać „inicjały, szkoła”.
    ("0001", Voivodeship.MAZOWIECKIE, SHARED_SCHOOL, True, True, True),
    # Niepełnoletni ze zgodą własną, ale **bez** zgody opiekuna: tryb „pełne dane” ma go zostawić
    # przy pseudonimie, choć jest laureatem i sam się zgodził.
    ("0002", Voivodeship.MAZOWIECKIE, SHARED_SCHOOL, False, True, False),
    ("0003", Voivodeship.MAZOWIECKIE, SHARED_SCHOOL, False, False, True),
    ("0004", Voivodeship.MAZOWIECKIE, "Liceum testowe nr 4", False, False, True),
    ("0005", Voivodeship.MALOPOLSKIE, "Liceum testowe nr 5", False, False, True),
    ("0006", Voivodeship.MALOPOLSKIE, "Liceum testowe nr 6", False, False, True),
    ("0007", Voivodeship.MALOPOLSKIE, "Liceum testowe nr 7", False, False, True),
    ("0008", Voivodeship.POMORSKIE, "Liceum testowe nr 8", False, False, True),
    ("0009", Voivodeship.MAZOWIECKIE, "Liceum testowe nr 9", False, False, True),
)

#: Uzasadnienie decyzji komitetu. Więz ``competitions_stageentry_manual_reason_required`` nie
#: dopuszcza decyzji bez uzasadnienia, a ``MIN_MANUAL_QUALIFICATION_REASON`` to dziesięć znaków.
MANUAL_REASON = "Decyzja komitetu na potrzeby fikstury testowej."


# --- etapy ----------------------------------------------------------------------------------------
#
# Cztery etapy w kształcie produkcji (§ 1.2.1) i **cztery różne tryby progu**, po jednym na etap –
# bo o równość dwóch dróg pyta się osobno dla każdego trybu:
#
# - trening: ``HYBRID`` – etap poza torem zawodów, którego progu migracja świadomie nie przepisuje,
# - eliminacje: ``TOP_N_PER_DISTRICT`` – jedyny tryb dzielący pole na grupy,
# - okręgowy: ``TOP_N`` w etapie w formie rozmowy, czyli **bez ani jednego zadania**: komplet zer
#   i próg, który nie kwalifikuje nikogo (dzisiejszy stan, ``docs/BACKLOG.md``),
# - finał: ``MIN_POINTS`` z remisem dokładnie na progu.

#: Punkty w treningu: kod uczestnika → oceny kolejnych zadań (``None`` = brak pracy).
TRAINING_SCORES = {"0001": (6,), "0007": (None,)}

#: Punkty eliminacji. Suma jest tym, co rozstrzyga próg, więc rozkład jest tu przedmiotem:
#: 0001 wygrywa okręg, 0002 i 0003 remisują **dokładnie na progu** (próg to wartość, a nie miejsce,
#: więc przechodzą oboje mimo ``top_n=2``), 0008 nie ma ani jednej pracy (komplet zer nie
#: kwalifikuje nawet wtedy, gdy jest jedynym wpisem w województwie), a 0009 ma wynik najwyższy
#: w okręgu i jest zdyskwalifikowany – czyli nie zajmuje miejsca w „top N”.
ELIM_SCORES = {
    "0001": (6, 6, 6),
    "0002": (6, 5, 2),
    "0003": (5, 6, 2),
    "0004": (2, 2, 0),
    "0005": (6, 6, 5),
    "0006": (5, 5, 5),
    "0007": (0, 0, 0),
    "0008": (None, None, None),
    "0009": (6, 6, 6),
}

#: Stany wpisów eliminacji odbiegające od ``REGISTERED``.
ELIM_STATUS = {"0009": StageEntryStatus.DISQUALIFIED}

#: Decyzje komitetu w eliminacjach – w **obie** strony: 0004 nie mieści się w progu i przechodzi,
#: 0006 mieści się i nie przechodzi.
ELIM_MANUAL = {
    "0004": ManualQualification.QUALIFIED,
    "0006": ManualQualification.NOT_QUALIFIED,
}

#: Wpisy etapu okręgowego założone z góry, wraz z decyzjami komisji. Wpisu 0004 tu **nie ma**
#: celowo: zakłada go dopiero kwalifikacja eliminacji (``_sync_next_stage``). Wpis 0006 jest
#: odwrotnym przypadkiem – jego właściciel kwalifikację stracił decyzją komitetu, więc pusty wpis
#: ma zostać z etapu okręgowego **usunięty**. Obie rzeczy mają wyjść tak samo obiema drogami.
DISTRICT_MANUAL = {
    "0001": ManualQualification.QUALIFIED,
    "0002": ManualQualification.QUALIFIED,
    "0003": ManualQualification.NOT_QUALIFIED,
    "0005": ManualQualification.QUALIFIED,
    "0006": ManualQualification.NONE,
}

#: Punkty finału. 0002 ma **dokładnie** próg (``min_points=8``), więc przechodzi – „co najmniej”
#: znaczy „co najmniej”, a nie „więcej niż”.
FINAL_SCORES = {"0001": (6, 6), "0002": (6, 2), "0005": (2, None)}

#: Próg finału. Stała, bo czyta ją i fikstura, i asercja o remisie na progu.
FINAL_MIN_POINTS = 8


def public_code(competition, suffix: str) -> str:
    """Kod publiczny uczestnika: prefiks **konkursu** plus jawny numer z fikstury."""
    return f"{competition.public_code_prefix}{suffix}"


def _birth_date(adult: bool) -> date:
    """Data urodzenia uczestnika fikstury – o tyle lat wstecz, ile mówi ``adult``.

    Dzień i miesiąc są **dzisiejsze**, więc osoba „pełnoletnia” ma dokładnie 25 lat, a nie „25 lat
    i kawałek”: od wydania 0.30.0 pełnoletność liczy się kalendarzowo, a fikstura złota ma dawać
    ten sam snapshot niezależnie od dnia, w którym chodzą testy. 29 lutego zamienia się na
    1 marca tą samą drogą, co w regule wieku.
    """
    today = timezone.localdate()
    year = today.year - (ADULT_AGE_YEARS if adult else MINOR_AGE_YEARS)
    try:
        return today.replace(year=year)
    except ValueError:  # 29 lutego w roku nieprzestępnym
        return date(year, 3, 1)


def _people(competition) -> dict[str, Participant]:
    """Uczestnicy fikstury, po jednym na wiersz ``PEOPLE``, zaindeksowani kodem publicznym."""
    people: dict[str, Participant] = {}
    for suffix, district, school, adult, publish_full_name, guardian_consent in PEOPLE:
        participant = ParticipantFactory(
            competition=competition,
            public_code=public_code(competition, suffix),
            user__email=f"snapshot-{suffix}@{MAIL_DOMAIN}",
            user__first_name="Uczestnik",
            user__last_name=f"Testowy {suffix}",
            school=school,
            district=district,
            birth_date=_birth_date(adult),
            publish_full_name=publish_full_name,
            guardian_consent=guardian_consent,
        )
        people[suffix] = participant
    return people


def _stage(competition, edition, *, kind, index_key, stage_format=StageFormat.SUBMISSIONS, problems=0):
    """Etap ze skalą 0/2/5/6 i oknem reklamacji **zamkniętym** – tak, jak etap po sezonie."""
    opens_at = timezone.now() - timedelta(days=STAGE_AGE_DAYS[index_key])
    stage = StageFactory(
        competition=competition,
        edition=edition,
        kind=kind,
        format=stage_format,
        opens_at=opens_at,
    )
    ScoringScaleFactory(competition=competition, stage=stage)
    for number in range(1, problems + 1):
        ProblemFactory(competition=competition, stage=stage, number=number, title=f"Zadanie {number}")
    return stage


def _training_stage(competition, edition) -> Stage:
    """Piaskownica treningowa: otwarta bez końca (``TRAINING_DEADLINE``, rok 2099).

    Termin bierzemy z modelu, a nie z literału – tak samo, jak robi to ``golden.py``.
    """
    stage = StageFactory(
        competition=competition,
        edition=edition,
        kind=StageKind.TRAINING,
        format=StageFormat.SUBMISSIONS,
        opens_at=timezone.now() - timedelta(days=STAGE_AGE_DAYS["elim"]),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE + timedelta(days=14),
        appeal_window_opens_at=TRAINING_DEADLINE + timedelta(days=16),
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=23),
    )
    ScoringScaleFactory(competition=competition, stage=stage)
    ProblemFactory(competition=competition, stage=stage, number=1, title="Zadanie treningowe")
    return stage


def _entry(competition, stage, participant, *, status=StageEntryStatus.REGISTERED, manual=""):
    values = {"status": status}
    if manual:
        values |= {"manual_qualification": manual, "manual_qualification_reason": MANUAL_REASON}
    return StageEntryFactory(competition=competition, participant=participant, stage=stage, **values)


def _grade(competition, entry, problems, scores) -> None:
    """Komplet prac oddanych i ocenionych. ``None`` znaczy „nie oddał”, czyli zero punktów.

    Praca jest w stanie ``FINAL`` i ma ``FinalGrade``, bo tabelę wyników ogłasza się dopiero po
    zamknięciu oceniania – wpis bez oceny wywróciłby przeliczenie na ``STAGE_NOT_FINALIZED``.
    Ocena końcowa jest jedynym źródłem punktów także po reklamacji: komisja odwoławcza zmienia
    właśnie ``FinalGrade``, więc „ocena po reklamacji” to w tej fiksturze po prostu inna liczba.
    """
    for problem, score in zip(problems, scores, strict=True):
        if score is None:
            continue
        submission = SubmissionFactory(
            competition=competition, entry=entry, problem=problem, status=SubmissionStatus.FINAL
        )
        FinalGradeFactory(competition=competition, submission=submission, score=score)


def _fill(competition, stage, people, scores, *, statuses=None, manual=None) -> None:
    problems = list(stage.problems.order_by("number", "id"))
    for suffix, values in scores.items():
        entry = _entry(
            competition,
            stage,
            people[suffix],
            status=(statuses or {}).get(suffix, StageEntryStatus.REGISTERED),
            manual=(manual or {}).get(suffix, ManualQualification.NONE),
        )
        _grade(competition, entry, problems, values)


def build_results_world(competition) -> ResultsWorld:
    """Składa całą edycję: cztery etapy, dziewięcioro uczestników, komplet ocen i decyzji.

    Jedna funkcja, a nie cztery fikstury: tabela każdego etapu zależy od kwalifikacji
    poprzedniego (``_sync_next_stage`` zakłada i sprząta wpisy), więc świat zbudowany z połowy
    części odpowiadałby inaczej niż produkcja i nie sprawdzałby niczego.
    """
    edition = CurrentEditionFactory(competition=competition, year_label=EDITION_LABEL)
    training = _training_stage(competition, edition)
    elim = _stage(competition, edition, kind=StageKind.ELIM, index_key="elim", problems=3)
    district = _stage(
        competition,
        edition,
        kind=StageKind.DISTRICT,
        index_key="district",
        stage_format=StageFormat.INTERVIEW,
    )
    final = _stage(competition, edition, kind=StageKind.FINAL, index_key="final", problems=2)

    QualificationRuleFactory(
        competition=competition,
        stage=training,
        mode=QualificationMode.HYBRID,
        min_points=2,
        top_n=1,
    )
    QualificationRuleFactory(
        competition=competition,
        stage=elim,
        mode=QualificationMode.TOP_N_PER_DISTRICT,
        min_points=None,
        top_n=2,
    )
    QualificationRuleFactory(
        competition=competition,
        stage=district,
        mode=QualificationMode.TOP_N,
        min_points=None,
        top_n=3,
    )
    QualificationRuleFactory(
        competition=competition,
        stage=final,
        mode=QualificationMode.MIN_POINTS,
        min_points=FINAL_MIN_POINTS,
    )

    people = _people(competition)
    _fill(competition, training, people, TRAINING_SCORES)
    _fill(competition, elim, people, ELIM_SCORES, statuses=ELIM_STATUS, manual=ELIM_MANUAL)
    for suffix, decision in DISTRICT_MANUAL.items():
        _entry(competition, district, people[suffix], manual=decision)
    _fill(competition, final, people, FINAL_SCORES)
    return ResultsWorld(
        competition=competition,
        edition=edition,
        training=training,
        elim=elim,
        district=district,
        final=final,
        participants=people,
    )


def write_pipeline_rows() -> None:
    """Zapisuje przebieg jako dane – **kodem migracji**, a nie własną pętlą.

    Migracja ``competitions.0024`` wpisuje kroki i reguły przejścia tym edycjom, które w bazie
    stały w chwili wdrożenia; edycja zbudowana fabryką w teście powstaje po niej, więc wierszy
    nie ma. Zamiast przewijać migracje (test transakcyjny, DDL po DML – robi to już
    ``apps/competitions/tests/test_pipeline_migration.py``) wołamy **to samo ciało** na żywym
    rejestrze modeli: ``forwards`` czyta wyłącznie ``apps.get_model`` i nie dotyka
    ``schema_editor``, więc rejestr produkcyjny jest dla niego tym samym, co historyczny.

    To jest istota testu § 5.2: droga „z danych” ma biec po wierszach, które napisze **produkcja**,
    a nie po wierszach, które test uznał za ich wierny odpowiednik. Gdyby migracja rozumiała
    „N na województwo” inaczej niż serwis, obie kopie tej samej pomyłki nie zgodziłyby się tu ze
    sobą – bo druga kopia w ogóle nie powstaje.
    """
    pipeline_from_stages.forwards(django_apps, None)


# --- fikstura brzegowa (§ 5.2, zestaw drugi) ------------------------------------------------------
#
# Dziewięć przypadków, każdy na osobnym, jednoetapowym świecie. Nie są rozszerzeniem świata wyżej,
# bo każdy z nich jest o **jednej** decyzji: dołożone do wspólnej edycji mieszałyby się ze sobą
# i pierwsze pytanie o przyczynę różnicy zostawałoby bez odpowiedzi.
#
# Wiersz przypadku: tryb progu, parametry, forma etapu, liczba zadań i wpisy. Wpis to
# ``(numer, oceny, województwo, stan, decyzja komitetu)``.

_MZ = Voivodeship.MAZOWIECKIE
_MP = Voivodeship.MALOPOLSKIE
_NONE = ManualQualification.NONE

EDGE_CASES: dict[str, dict] = {
    # Minimum punktów: porównanie „co najmniej”, bez żadnego odcięcia po miejscach.
    "min_points": {
        "mode": QualificationMode.MIN_POINTS,
        "min_points": 4,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (2, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (0, 0), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Najlepszych N: próg jest **wartością** zajmującą miejsce N, a nie samym miejscem.
    "top_n": {
        "mode": QualificationMode.TOP_N,
        "top_n": 2,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (6, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (2, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("4", (0, 0), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # N na województwo: jedyny dzisiejszy tryb dzielący pole na grupy.
    "top_n_per_district": {
        "mode": QualificationMode.TOP_N_PER_DISTRICT,
        "top_n": 1,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (6, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (2, 2), _MP, StageEntryStatus.REGISTERED, _NONE),
            ("4", (2, 0), _MP, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Hybryda: iloczyn dwóch warunków **wewnątrz** jednej reguły.
    "hybrid": {
        "mode": QualificationMode.HYBRID,
        "min_points": 10,
        "top_n": 3,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (6, 5), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (5, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("4", (2, 0), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Remis na progu: trzech z tą samą sumą przy ``top_n=2`` – wchodzą wszyscy trzej.
    "tie_at_cutoff": {
        "mode": QualificationMode.TOP_N,
        "top_n": 2,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (5, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (2, 5), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("4", (6, 0), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Komplet zer: „najlepszy” z samych zer nie kwalifikuje się mimo wolnych miejsc.
    "all_zeros": {
        "mode": QualificationMode.TOP_N,
        "top_n": 3,
        "problems": 2,
        "entries": (
            ("1", (0, 0), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (None, None), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Zdyskwalifikowany z najwyższym wynikiem: nie zajmuje miejsca w „top N”.
    "disqualified": {
        "mode": QualificationMode.TOP_N,
        "top_n": 1,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.DISQUALIFIED, _NONE),
            ("2", (5, 5), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("3", (2, 2), _MZ, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Decyzja komitetu w obie strony – bije próg, ale nie podnosi zdyskwalifikowanego.
    "manual_both_ways": {
        "mode": QualificationMode.MIN_POINTS,
        "min_points": 4,
        "problems": 2,
        "entries": (
            ("1", (6, 6), _MZ, StageEntryStatus.REGISTERED, ManualQualification.NOT_QUALIFIED),
            ("2", (0, 0), _MZ, StageEntryStatus.REGISTERED, ManualQualification.QUALIFIED),
            ("3", (6, 6), _MZ, StageEntryStatus.DISQUALIFIED, ManualQualification.QUALIFIED),
        ),
    },
    # Etap w formie testu online: sumę podaje ``apps.quiz``, a nie oceny zadań. Test bez ani
    # jednego podejścia daje wszystkim zero – i o to tu chodzi: gałąź ma być ta sama obiema drogami.
    "quiz_stage": {
        "mode": QualificationMode.MIN_POINTS,
        "min_points": 1,
        "format": StageFormat.QUIZ,
        "problems": 0,
        "entries": (
            ("1", (), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (), _MP, StageEntryStatus.REGISTERED, _NONE),
        ),
    },
    # Etap bez ani jednego zadania (dzisiejsza rozmowa kwalifikacyjna): komplet zer i pusty
    # słownik ``points`` w każdym wierszu tabeli.
    "stage_without_problems": {
        "mode": QualificationMode.TOP_N,
        "top_n": 2,
        "format": StageFormat.INTERVIEW,
        "problems": 0,
        "entries": (
            ("1", (), _MZ, StageEntryStatus.REGISTERED, _NONE),
            ("2", (), _MP, StageEntryStatus.REGISTERED, ManualQualification.QUALIFIED),
        ),
    },
}


def build_edge_stage(competition, name: str) -> Stage:
    """Jednoetapowy świat jednego przypadku brzegowego. Kroki przebiegu dopisuje wołający."""
    case = EDGE_CASES[name]
    edition = CurrentEditionFactory(competition=competition, year_label=f"Edycja brzegowa – {name}")
    opens_at = timezone.now() - timedelta(days=STAGE_AGE_DAYS["elim"])
    stage = StageFactory(
        competition=competition,
        edition=edition,
        kind=StageKind.ELIM,
        format=case.get("format", StageFormat.SUBMISSIONS),
        opens_at=opens_at,
    )
    ScoringScaleFactory(competition=competition, stage=stage)
    QualificationRuleFactory(
        competition=competition,
        stage=stage,
        mode=case["mode"],
        min_points=case.get("min_points"),
        top_n=case.get("top_n"),
    )
    problems = [
        ProblemFactory(competition=competition, stage=stage, number=number, title=f"Zadanie {number}")
        for number in range(1, case["problems"] + 1)
    ]
    for suffix, scores, district, status, manual in case["entries"]:
        participant = ParticipantFactory(
            competition=competition,
            public_code=public_code(competition, f"E{suffix}"),
            user__email=f"brzeg-{name}-{suffix}@{MAIL_DOMAIN}",
            user__first_name="Uczestnik",
            user__last_name=f"Brzegowy {suffix}",
            district=district,
            birth_date=_birth_date(False),
        )
        entry = _entry(competition, stage, participant, status=status, manual=manual)
        _grade(competition, entry, problems, scores)
    return stage
