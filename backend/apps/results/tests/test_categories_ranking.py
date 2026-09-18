"""Rankingi i snapshot per kategoria (T32, ``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.4).

Kategoria dotyka wyników w **trzech** miejscach i ani jednym więcej: ranking (``_rank_rows``
z podziałem), próg (reguła przejścia zawężona kategorią – to jest T30) i snapshot (jeden dodatkowy
klucz w wierszu). Ten plik pilnuje pierwszego i trzeciego, a przede wszystkim pilnuje **granicy**:

> Konkurs bez kategorii nie zyskuje ani jednego klucza w wierszu, ani jednego klucza w snapshocie
> i ani jednej innej liczby w kolumnie „miejsce”.

Dlatego pierwsza grupa asercji porównuje zbiory kluczy **na równość**, a nie na zawieranie: test
na podzbiór przepuściłby dołożone pole, czyli dokładnie ten błąd, który wyszedłby dopiero
w ogłoszonej tabeli Olimpiady Kwantowej (§ 5.2).
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import UserFactory
from apps.competitions.models import Category, QualificationMode, StageEntry
from apps.results.models import Anonymization
from apps.results.serializers import PublicResultRowSerializer, StageResultRowSerializer
from apps.results.services import (
    CATEGORIES_FLAG,
    MIN_SCHOOL_GROUP,
    build_snapshot,
    categories_enabled,
    compute_stage_results,
    publish_results,
)
from apps.results.statistics import build_statistics

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

#: Klucze wiersza roboczego Konkursu #1 – komplet, co do jednego. Lista jest tu **literałem**
#: z rozmysłem: to jest kontrakt, którego etap 2 nie ma prawa ruszyć, a nie wyliczenie z kodu,
#: które zmieni się razem z nim.
ROW_KEYS = {
    "entry_id",
    "participant_id",
    "public_code",
    "first_name",
    "last_name",
    "school",
    "district",
    "publish_full_name",
    "guardian_consent",
    "is_adult",
    "status",
    "manual_qualification",
    "points",
    "total",
    "rank",
}

#: Klucze wiersza snapshotu przy anonimizacji „kod uczestnika”. Tryby ``INITIALS_SCHOOL``
#: i ``FULL`` mają ten sam zestaw bez ``district``.
SNAPSHOT_KEYS = {"rank", "display", "points", "total", "qualified", "manual", "district"}


def enable(competition):
    """Przestawia konkurs na kategorie. Zapis w bazie, tą samą drogą, którą zrobi to wdrożenie."""
    competition.feature_flags = {**(competition.feature_flags or {}), CATEGORIES_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def make_category(competition, code: str, position: int = 0, **kwargs) -> Category:
    kwargs.setdefault("name", code.title())
    return Category.objects.create(competition=competition, code=code, position=position, **kwargs)


def assign(entry, category) -> None:
    """Kategoria wpisu wprost w bazie – ekran, który ją nadaje, powstaje dopiero w T27."""
    StageEntry.objects.filter(pk=entry.pk).update(category=category)


def ranks(rows: list[dict]) -> list[tuple[str, int]]:
    return [(row["public_code"], row["rank"]) for row in rows]


# --- (a) Konkurs #1: ani jednego nowego klucza ----------------------------------------------------


def test_categories_are_off_for_competition_one(competition):
    assert competition.has_feature(CATEGORIES_FLAG) is False
    assert categories_enabled(competition) is False
    assert categories_enabled(None) is False


def test_the_working_row_keeps_exactly_its_keys(competition):
    """Porównanie na równość, nie na zawieranie – dołożony klucz ma tu paść, a nie przejść."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5])

    rows = compute_stage_results(stage)

    assert set(rows[0]) == ROW_KEYS


def test_the_snapshot_keeps_exactly_its_keys(competition):
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5])
    rows = compute_stage_results(stage)

    snapshot = build_snapshot(rows, Anonymization.CODE)

    assert set(snapshot[0]) == SNAPSHOT_KEYS
    assert set(build_snapshot(rows, Anonymization.INITIALS_SCHOOL)[0]) == SNAPSHOT_KEYS - {"district"}


def test_one_table_and_todays_places_without_categories(competition):
    """Miejsca: malejąco po sumie, remis = to samo miejsce (1, 1, 3). Bez zmiany."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6], public_code="OLM-AAA111")
    graded_entry(stage, [5], public_code="OLM-BBB222")
    graded_entry(stage, [5], public_code="OLM-CCC333")
    graded_entry(stage, [2], public_code="OLM-DDD444")

    rows = compute_stage_results(stage)

    assert ranks(rows) == [("OLM-AAA111", 1), ("OLM-BBB222", 2), ("OLM-CCC333", 2), ("OLM-DDD444", 4)]


def test_public_code_stays_the_last_sort_key(competition):
    """Porządek wewnątrz remisu jest po ``public_code`` – tabela ma być powtarzalna co do pliku."""
    stage = make_stage(problems=1)
    graded_entry(stage, [5], public_code="OLM-ZZZ999")
    graded_entry(stage, [5], public_code="OLM-AAA111")

    rows = compute_stage_results(stage)

    assert [row["public_code"] for row in rows] == ["OLM-AAA111", "OLM-ZZZ999"]


# --- (b) konkurs z kategoriami: osobny ranking w każdej -------------------------------------------


def test_each_category_gets_its_own_places_from_one(competition):
    """Uczestnik ma być pierwszy **w swojej kategorii**, a nie dwusetny w tabeli zbiorczej."""
    stage = make_stage(problems=1)
    junior = make_category(competition, "podstawowa", position=1)
    senior = make_category(competition, "ponadpodstawowa", position=2)
    assign(graded_entry(stage, [2], public_code="OLM-JUN111"), junior)
    assign(graded_entry(stage, [1], public_code="OLM-JUN222"), junior)
    assign(graded_entry(stage, [6], public_code="OLM-SEN111"), senior)
    assign(graded_entry(stage, [5], public_code="OLM-SEN222"), senior)
    enable(competition)

    rows = compute_stage_results(stage)

    assert ranks(rows) == [
        ("OLM-JUN111", 1),
        ("OLM-JUN222", 2),
        ("OLM-SEN111", 1),
        ("OLM-SEN222", 2),
    ]


def test_ties_inside_a_category_share_a_place(competition):
    """Remis zostaje remisem także w grupie – i następne miejsce jest liczone od początku grupy."""
    stage = make_stage(problems=1)
    junior = make_category(competition, "podstawowa", position=1)
    for code, score in (("OLM-AAA111", 5), ("OLM-BBB222", 5), ("OLM-CCC333", 2)):
        assign(graded_entry(stage, [score], public_code=code), junior)
    enable(competition)

    rows = compute_stage_results(stage)

    assert ranks(rows) == [("OLM-AAA111", 1), ("OLM-BBB222", 1), ("OLM-CCC333", 3)]


def test_groups_follow_the_position_set_by_the_organiser(competition):
    """Kolejność grup jest decyzją organizatora (``Category.position``), a nie alfabetu."""
    stage = make_stage(problems=1)
    first = make_category(competition, "zaawansowana", position=1)
    second = make_category(competition, "alfa", position=2)
    assign(graded_entry(stage, [6], public_code="OLM-ZAA111"), first)
    assign(graded_entry(stage, [6], public_code="OLM-ALF111"), second)
    enable(competition)

    rows = compute_stage_results(stage)

    assert [row["category"] for row in rows] == ["Zaawansowana", "Alfa"]


def test_entries_without_a_category_form_one_group(competition):
    """Wpis bez kategorii nie wypada z rankingu – dostaje grupę „bez kategorii” i miejsca od 1."""
    stage = make_stage(problems=1)
    junior = make_category(competition, "podstawowa", position=1)
    assign(graded_entry(stage, [2], public_code="OLM-JUN111"), junior)
    graded_entry(stage, [6], public_code="OLM-NON111")
    graded_entry(stage, [5], public_code="OLM-NON222")
    enable(competition)

    rows = compute_stage_results(stage)

    assert ranks(rows) == [("OLM-NON111", 1), ("OLM-NON222", 2), ("OLM-JUN111", 1)]


def test_the_row_carries_the_label_and_the_key(competition):
    """Etykieta do wyświetlenia i identyfikator do grupowania – dwie różne potrzeby, dwa klucze."""
    stage = make_stage(problems=1)
    junior = make_category(competition, "podstawowa", position=1)
    entry = graded_entry(stage, [6])
    assign(entry, junior)
    enable(competition)

    row = compute_stage_results(stage)[0]

    assert row["category"] == "Podstawowa"
    assert row["category_id"] == junior.pk
    assert row["category_position"] == 1


# --- (c) snapshot ---------------------------------------------------------------------------------


def test_the_snapshot_gains_the_category_only_with_the_flag(competition):
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=1)
    junior = make_category(competition, "podstawowa", position=1)
    assign(graded_entry(stage, [6]), junior)
    enable(competition)

    snapshot = build_snapshot(compute_stage_results(stage), Anonymization.CODE)

    assert set(snapshot[0]) == SNAPSHOT_KEYS | {"category"}
    assert snapshot[0]["category"] == "Podstawowa"


def test_the_published_table_carries_the_category(competition):
    """Cała droga od końca: publikacja zamraża tabelę razem z kategorią, bez danych osobowych."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=1)
    junior = make_category(competition, "podstawowa", position=1)
    assign(graded_entry(stage, [6]), junior)
    enable(competition)

    publication = publish_results(stage, None, Anonymization.CODE)

    assert publication.snapshot[0]["category"] == "Podstawowa"
    assert "first_name" not in publication.snapshot[0]


def test_the_serializer_passes_the_category_through_and_skips_it_when_absent(competition):
    """Ten sam wzorzec, co przy okręgu: brak klucza to pominięte pole, a nie błąd walidacji."""
    with_category = PublicResultRowSerializer(
        {
            "rank": 1,
            "display": "OLM-AAA111",
            "points": {},
            "total": 6,
            "qualified": True,
            "category": "Podstawowa",
        }
    ).data
    without = PublicResultRowSerializer(
        {"rank": 1, "display": "OLM-AAA111", "points": {}, "total": 6, "qualified": True}
    ).data

    assert with_category["category"] == "Podstawowa"
    assert "category" not in without


def test_the_coordinator_row_serializer_skips_the_category_too(competition):
    row = StageResultRowSerializer(
        {
            "rank": 1,
            "entry_id": 1,
            "public_code": "OLM-AAA111",
            "first_name": "",
            "last_name": "",
            "school": "",
            "district": "",
            "status": "REGISTERED",
            "points": {},
            "total": 6,
        }
    ).data

    assert "category" not in row


# --- (d) anonimizacja bez zmian -------------------------------------------------------------------


def test_k_anonymity_of_initials_and_school_is_untouched(competition):
    """``MIN_SCHOOL_GROUP`` zostaje trójką, a kategoria nie ma na tę regułę żadnego wpływu."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=1)
    junior = make_category(competition, "podstawowa", position=1)
    assign(
        graded_entry(stage, [6], school="XIV LO", user=UserFactory(first_name="Jan", last_name="Kowalski")),
        junior,
    )
    enable(competition)

    snapshot = build_snapshot(compute_stage_results(stage), Anonymization.INITIALS_SCHOOL)

    assert MIN_SCHOOL_GROUP == 3
    # Jedna osoba z tej szkoły – „J.K., XIV LO” byłoby wskazaniem palcem, więc zostaje pseudonim.
    assert snapshot[0]["display"].startswith("OLM-")


# --- (e) statystyki -------------------------------------------------------------------------------


def test_statistics_have_no_categories_for_competition_one(competition):
    """Pusta lista, a nie brak klucza: kształt odpowiedzi ma być jeden dla obu konkursów."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=1)
    graded_entry(stage, [6], district="mazowieckie")
    publish_results(stage, None, Anonymization.CODE)

    assert build_statistics()[0]["categories"] == []


def test_statistics_count_the_categories_of_the_published_table(competition):
    """Liczby z zamrożonego snapshotu i z niczego innego – tak samo, jak przy województwach."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=1)
    junior = make_category(competition, "podstawowa", position=1)
    senior = make_category(competition, "ponadpodstawowa", position=2)
    assign(graded_entry(stage, [6], district="mazowieckie"), junior)
    assign(graded_entry(stage, [5], district="mazowieckie"), senior)
    assign(graded_entry(stage, [2], district="malopolskie"), senior)
    enable(competition)
    publish_results(stage, None, Anonymization.CODE)

    categories = build_statistics()[0]["categories"]

    assert [(row["label"], row["count"]) for row in categories] == [("Podstawowa", 1), ("Ponadpodstawowa", 2)]
