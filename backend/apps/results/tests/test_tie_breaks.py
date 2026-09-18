"""Porządek rozstrzygania remisów w tabeli wyników (T40, ``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.6 c).

Dwa pytania i oba trzeba zadać w tej kolejności:

1. **Czy etap bez ani jednego kryterium zachowuje się dokładnie jak dziś?** Remis to wspólne
   miejsce, a porządek wewnątrz remisu jest po ``public_code`` – i tak samo ma być wtedy, gdy
   wiersze ``TieBreak`` w bazie są, ale konkurs nie ma flagi ``weighted_scoring``. To jest
   wymaganie nadrzędne (§ 0.1) i dlatego stoi w tym pliku pierwsze, razem z asercją o **zerze**
   dodatkowych zapytań (§ 5.6).
2. Czy przy włączonej fladze kryteria rozstrzygają w kolejności organizatora, brak wartości ląduje
   na końcu, a ``public_code`` zostaje **ostatnim** kluczem sortowania i nigdy nie awansuje do roli
   kryterium – bo „dwie publikacje tych samych danych muszą dać ten sam plik”.

Remisy wchodzą za flagą ``weighted_scoring``, bo § 1.2.6 opisuje wagi, punkty ujemne i remisy jako
**jedną** decyzję organizatora. Sam wiersz ``TieBreak`` przy wyłączonej fladze jest więc martwym
zapisem – i to też jest tu sprawdzane, bo inaczej import albo migracja mogłyby zmienić ogłoszoną
tabelę bez ani jednej decyzji organizatora.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.competitions.models import TieBreak, TieBreakKey
from apps.competitions.services import WEIGHTED_SCORING_FLAG
from apps.results.services import (
    TieBreakSpec,
    _rank_rows,
    compute_stage_results,
    tie_break_keys,
)
from apps.submissions.models import Submission

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), WEIGHTED_SCORING_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def add_rule(stage, key, *, position=1, descending=True, **kwargs) -> TieBreak:
    return TieBreak.objects.create(stage=stage, key=key, position=position, descending=descending, **kwargs)


def places(stage) -> list[tuple[str, int]]:
    """Tabela etapu w postaci, w której czyta się ją jak wydruk: kod uczestnika i jego miejsce."""
    return [(row["public_code"], row["rank"]) for row in compute_stage_results(stage)]


def row(code, total, *, points=None, components=None, entry_id=0, **extra) -> dict:
    """Wiersz roboczy w minimalnej postaci – do testów samego ``_rank_rows``, bez bazy."""
    return {
        "entry_id": entry_id,
        "public_code": code,
        "total": total,
        "points": points or {},
        "components": components or {},
    } | extra


# --- (a) etap bez kryteriów: ani jednej zmiany ----------------------------------------------------


def test_a_tie_shares_the_place_and_prints_by_public_code(competition):
    """Dzisiejsza reguła, przepisana co do liczby: ta sama suma to to samo miejsce (1, 1, 3)."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5], public_code="OLM-C")
    graded_entry(stage, [5, 6], public_code="OLM-A")
    graded_entry(stage, [2, 0], public_code="OLM-B")

    assert places(stage) == [("OLM-A", 1), ("OLM-C", 1), ("OLM-B", 3)]


def test_tie_break_rows_change_nothing_without_the_flag(competition):
    """Wiersz ``TieBreak`` w konkursie bez flagi jest martwym zapisem, a nie cichą zmianą tabeli."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5], public_code="OLM-C")
    graded_entry(stage, [5, 6], public_code="OLM-A")
    add_rule(stage, TieBreakKey.PROBLEM_SCORE, problem=stage_problems(stage)[0])

    assert places(stage) == [("OLM-A", 1), ("OLM-C", 1)]


def test_the_flag_off_costs_no_query_at_all(competition, django_assert_num_queries):
    """Flagę czytamy **przed** bazą: etap z kryteriami w wyłączonym konkursie nie kosztuje zapytania."""
    stage = make_stage(problems=1)
    add_rule(stage, TieBreakKey.SOLVED_COUNT)

    with django_assert_num_queries(0):
        assert tie_break_keys(stage, competition=competition) == ()


def test_the_table_costs_the_same_with_and_without_tie_break_rows(competition):
    """Ten sam etap, te same liczby, ta sama liczba zapytań – kryteria nie dokładają ich bez flagi."""
    plain = make_stage(problems=2)
    graded_entry(plain, [6, 5], public_code="OLM-A")
    with_rules = make_stage(problems=2)
    graded_entry(with_rules, [6, 5], public_code="OLM-B")
    add_rule(with_rules, TieBreakKey.HIGHEST_SINGLE)

    with CaptureQueriesContext(connection) as without:
        compute_stage_results(plain)
    with CaptureQueriesContext(connection) as within:
        compute_stage_results(with_rules)

    assert len(within) == len(without)


# --- (b) kryteria rozstrzygają -------------------------------------------------------------------


def test_a_named_problem_breaks_the_tie(competition):
    """„Przy równej sumie wyżej ten, kto ma więcej w zadaniu 2” – regulamin zapisany jako wiersz."""
    enable(competition)
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5], public_code="OLM-A")
    graded_entry(stage, [5, 6], public_code="OLM-B")
    add_rule(stage, TieBreakKey.PROBLEM_SCORE, problem=stage_problems(stage)[1])

    assert places(stage) == [("OLM-B", 1), ("OLM-A", 2)]


def test_the_highest_single_score_breaks_the_tie(competition):
    enable(competition)
    stage = make_stage(problems=3)
    graded_entry(stage, [5, 5, 2], public_code="OLM-A")
    graded_entry(stage, [6, 4, 2], public_code="OLM-B")
    add_rule(stage, TieBreakKey.HIGHEST_SINGLE)

    assert places(stage) == [("OLM-B", 1), ("OLM-A", 2)]


def test_the_number_of_full_scores_breaks_the_tie(competition):
    """Pełny wynik to maksimum skali etapu (0/2/5/6), a nie „dużo punktów”."""
    enable(competition)
    stage = make_stage(problems=3)
    graded_entry(stage, [6, 6, 0], public_code="OLM-A")
    graded_entry(stage, [6, 5, 1], public_code="OLM-B")
    add_rule(stage, TieBreakKey.SOLVED_COUNT)

    assert places(stage) == [("OLM-A", 1), ("OLM-B", 2)]


def test_the_earlier_last_submission_breaks_the_tie(competition):
    """Kierunek nie wynika z kryterium: przy czasie „lepiej” znaczy wcześniej, czyli ``descending=False``."""
    enable(competition)
    stage = make_stage(problems=2)
    early = graded_entry(stage, [6, 5], public_code="OLM-A")
    late = graded_entry(stage, [5, 6], public_code="OLM-B")
    now = timezone.now()
    Submission.objects.filter(entry=early).update(submitted_at=now - timedelta(days=3))
    Submission.objects.filter(entry=late).update(submitted_at=now - timedelta(days=1))
    add_rule(stage, TieBreakKey.SUBMITTED_AT, descending=False)

    assert places(stage) == [("OLM-A", 1), ("OLM-B", 2)]


def test_criteria_decide_in_the_organiser_order(competition):
    """Pierwsze kryterium rozstrzyga, drugie wchodzi dopiero przy równości na pierwszym."""
    enable(competition)
    stage = make_stage(problems=3)
    first, second, _third = stage_problems(stage)
    graded_entry(stage, [5, 6, 0], public_code="OLM-A")
    graded_entry(stage, [5, 0, 6], public_code="OLM-B")
    graded_entry(stage, [6, 5, 0], public_code="OLM-C")
    add_rule(stage, TieBreakKey.PROBLEM_SCORE, problem=first, position=1)
    add_rule(stage, TieBreakKey.PROBLEM_SCORE, problem=second, position=2)

    # Suma 11 u wszystkich trojga: rozstrzyga zadanie 1 (6 > 5), a między dwiema piątkami – zadanie 2.
    assert places(stage) == [("OLM-C", 1), ("OLM-A", 2), ("OLM-B", 3)]


def test_rows_equal_on_every_criterion_still_share_the_place(competition):
    """Kryterium nie ma prawa **stworzyć** porządku tam, gdzie regulamin go nie ma."""
    enable(competition)
    stage = make_stage(problems=2)
    graded_entry(stage, [5, 5], public_code="OLM-B")
    graded_entry(stage, [5, 5], public_code="OLM-A")
    add_rule(stage, TieBreakKey.HIGHEST_SINGLE)

    assert places(stage) == [("OLM-A", 1), ("OLM-B", 1)]


def test_the_public_code_is_never_a_criterion(competition):
    """Wspólne miejsce zostaje wspólne, a ``public_code`` układa tylko wydruk – tak jak przed etapem 2."""
    enable(competition)
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 6], public_code="OLM-Z")
    graded_entry(stage, [6, 6], public_code="OLM-A")
    add_rule(stage, TieBreakKey.SOLVED_COUNT)

    assert places(stage) == [("OLM-A", 1), ("OLM-Z", 1)]


def test_a_missing_value_goes_last_whichever_way_we_sort(competition):
    """Brak danych nie jest ani najlepszym, ani najgorszym wynikiem – nie ma wyprzedzać nikogo."""
    enable(competition)
    stage = make_stage(problems=2)
    submitted = graded_entry(stage, [0, 0], public_code="OLM-B")
    graded_entry(stage, [None, None], public_code="OLM-A")
    Submission.objects.filter(entry=submitted).update(submitted_at=timezone.now())
    rule = add_rule(stage, TieBreakKey.SUBMITTED_AT, descending=False)

    assert places(stage) == [("OLM-B", 1), ("OLM-A", 2)]

    rule.descending = True
    rule.save(update_fields=["descending"])

    assert places(stage) == [("OLM-B", 1), ("OLM-A", 2)]


# --- (c) „bez rozstrzygania”: ``NONE`` ucina listę ------------------------------------------------


def test_none_cuts_the_list_together_with_itself(competition):
    """Po ``NONE`` nie rozstrzyga już nic – kryteria za nim zostają w bazie, ale nie w kluczu."""
    enable(competition)
    stage = make_stage(problems=2)
    add_rule(stage, TieBreakKey.HIGHEST_SINGLE, position=1)
    add_rule(stage, TieBreakKey.NONE, position=2)
    add_rule(stage, TieBreakKey.SOLVED_COUNT, position=3)

    keys = tie_break_keys(stage, competition=competition)

    assert [spec.key for spec in keys] == [TieBreakKey.HIGHEST_SINGLE]


def test_none_in_the_first_place_means_todays_table(competition):
    enable(competition)
    stage = make_stage(problems=2)
    add_rule(stage, TieBreakKey.NONE, position=0)

    assert tie_break_keys(stage, competition=competition) == ()


def test_reading_the_criteria_costs_one_query(competition, django_assert_num_queries):
    """Lista kryteriów to jedno zapytanie na przeliczenie – nigdy jedno na wiersz tabeli."""
    enable(competition)
    stage = make_stage(problems=2)
    add_rule(stage, TieBreakKey.PROBLEM_SCORE, problem=stage_problems(stage)[0])

    with django_assert_num_queries(1):
        assert len(tie_break_keys(stage, competition=competition)) == 1


# --- (d) sam porządek: ``_rank_rows`` bez bazy ----------------------------------------------------


def test_rank_rows_without_criteria_is_todays_function():
    """Pusta krotka kryteriów ma dać listę identyczną co do wiersza i co do miejsca, jak przed etapem 2."""
    rows = [row("OLM-B", 10), row("OLM-A", 10), row("OLM-C", 3)]

    assert [(item["public_code"], item["rank"]) for item in _rank_rows(rows)] == [
        ("OLM-A", 1),
        ("OLM-B", 1),
        ("OLM-C", 3),
    ]


def test_rank_rows_reads_a_named_component():
    """Kryterium komponentowe czyta ``row["components"]`` – klucz, który dokłada sumowanie po formach."""
    rows = [
        row("OLM-A", 10, components={"7": 2, "8": 8}),
        row("OLM-B", 10, components={"7": 5, "8": 5}),
    ]
    spec = TieBreakSpec(key=TieBreakKey.COMPONENT_SCORE, component_id="7")

    assert [item["public_code"] for item in _rank_rows(rows, None, (spec,))] == ["OLM-B", "OLM-A"]


def test_rank_rows_puts_a_missing_component_last_in_both_directions():
    rows = [row("OLM-A", 10), row("OLM-B", 10, components={"7": 0})]

    for descending in (True, False):
        spec = TieBreakSpec(key=TieBreakKey.COMPONENT_SCORE, component_id="7", descending=descending)
        ordered = _rank_rows([dict(item) for item in rows], None, (spec,))
        assert [(item["public_code"], item["rank"]) for item in ordered] == [("OLM-B", 1), ("OLM-A", 2)]


def test_rank_rows_breaks_ties_inside_a_group_not_across_it():
    """Kategorie i remisy są niezależne: miejsca liczą się od 1 w każdej grupie, kryterium działa w niej."""
    rows = [
        row("OLM-A", 10, points={"1": 2}, category_id=1, category_position=1),
        row("OLM-B", 10, points={"1": 6}, category_id=1, category_position=1),
        row("OLM-C", 4, points={"1": 4}, category_id=2, category_position=2),
    ]
    spec = TieBreakSpec(key=TieBreakKey.PROBLEM_SCORE, problem_number="1")

    ordered = _rank_rows(rows, "category_id", (spec,))

    assert [(item["public_code"], item["rank"]) for item in ordered] == [
        ("OLM-B", 1),
        ("OLM-A", 2),
        ("OLM-C", 1),
    ]


def test_rank_rows_is_repeatable():
    """Dwie publikacje tych samych danych muszą dać ten sam plik – także z kryteriami."""
    spec = TieBreakSpec(key=TieBreakKey.HIGHEST_SINGLE)
    first = [row("OLM-B", 10, points={"1": 5}), row("OLM-A", 10, points={"1": 5})]
    second = [row("OLM-A", 10, points={"1": 5}), row("OLM-B", 10, points={"1": 5})]

    assert [item["public_code"] for item in _rank_rows(first, None, (spec,))] == ["OLM-A", "OLM-B"]
    assert [item["public_code"] for item in _rank_rows(second, None, (spec,))] == ["OLM-A", "OLM-B"]
