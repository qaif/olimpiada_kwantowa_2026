"""Arytmetyka nagród (``apps.medals.awards``) – czysta funkcja, bez bazy.

Wiersze mają kształt wierszy ``compute_stage_results``: ``entry_id``, ``status``, ``total``,
``points``. Pilnujemy reguł z MED-01 § 1: pule zaokrąglane w górę, próg będący wartością sumy (ten sam
wynik = ta sama nagroda), zero bez nagrody, zdyskwalifikowani poza polem, dwie polityki remisu
i dwa kryteria wyróżnienia.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.competitions.models import StageEntryStatus
from apps.medals.awards import (
    SchemeParams,
    compute_awards,
    country_table,
    cutoff,
    final_awards,
    medal_order,
    pool_size,
)
from apps.medals.models import Award, TiePolicy

G, S, B, HM, NONE = Award.GOLD, Award.SILVER, Award.BRONZE, Award.HONOURABLE, Award.NONE


def rows_for(totals, *, points=None, statuses=None):
    """Wiersze z sumami ``totals`` (entry_id = pozycja od 1)."""
    rows = []
    for index, total in enumerate(totals, start=1):
        rows.append(
            {
                "entry_id": index,
                "status": (statuses or {}).get(index, StageEntryStatus.QUALIFIED),
                "total": Decimal(total),
                "points": (points or {}).get(index, {}),
            }
        )
    return rows


def awards_of(totals, params=SchemeParams(), *, full_scores=None, **kwargs):
    awards, thresholds = compute_awards(rows_for(totals, **kwargs), params, full_scores)
    return [awards[index] for index in range(1, len(totals) + 1)], thresholds


def test_pool_size_rounds_up_and_never_exceeds_the_field():
    assert pool_size(100, Decimal("8")) == 8
    assert pool_size(20, Decimal("8")) == 2  # 1,6 → 2
    assert pool_size(7, Decimal("50")) == 4  # 3,5 → 4
    assert pool_size(3, Decimal("100")) == 3
    assert pool_size(0, Decimal("8")) == 0
    assert pool_size(10, Decimal("0")) == 0


def test_default_scheme_on_a_field_of_one_hundred_is_ipho_like():
    totals = list(range(100, 0, -1))  # 100 różnych wyników, bez remisów
    awards, thresholds = awards_of(totals, SchemeParams(hm_percent_of_best=None, hm_full_solution=False))

    assert awards.count(G) == 8
    assert awards.count(S) == 17
    assert awards.count(B) == 25
    assert awards.count(HM) == 0
    assert thresholds.slots == {G: 8, S: 25, B: 50}
    assert thresholds.cutoffs[G] == Decimal(93)
    assert thresholds.share(G) == Decimal("8.0")


@pytest.mark.parametrize("boundary", [G, S, B])
def test_a_tie_on_any_boundary_is_never_split(boundary):
    """Ten sam wynik = ta sama nagroda – remis na granicy wchodzi w całości (INCLUSIVE)."""
    totals = list(range(100, 0, -1))
    position = {G: 8, S: 25, B: 50}[boundary]
    totals[position] = totals[position - 1]  # pierwszy spoza puli remisuje z ostatnim w puli
    awards, _ = awards_of(totals, SchemeParams(hm_percent_of_best=None, hm_full_solution=False))

    assert awards[position] == awards[position - 1] == boundary


def test_exclusive_policy_keeps_the_tie_group_below_the_pool():
    totals = [10, 9, 9, 8, 1]  # pula złota: ceil(5 × 30 %) = 2, a grupa „9” przekroczyłaby ją
    params = SchemeParams(
        gold_percent=Decimal(30),
        silver_percent=Decimal(30),
        bronze_percent=Decimal(0),
        tie_policy=TiePolicy.EXCLUSIVE,
        hm_percent_of_best=None,
        hm_full_solution=False,
    )
    awards, thresholds = awards_of(totals, params)

    # Złoto: pula 2, ale grupa „9” by ją przekroczyła – zostaje sama „10”. Srebro: pula łączna 3
    # mieści „10, 9, 9”, więc grupa „9” dostaje srebro, a „8” jest już poza pulą.
    assert awards == [G, S, S, NONE, NONE]
    assert thresholds.cutoffs[G] == Decimal(10)


def test_inclusive_policy_lets_the_whole_tie_group_in():
    totals = [10, 9, 9, 8, 1]
    params = SchemeParams(
        gold_percent=Decimal(30),
        silver_percent=Decimal(30),
        bronze_percent=Decimal(0),
        hm_percent_of_best=None,
        hm_full_solution=False,
    )
    awards, _ = awards_of(totals, params)

    # Pula złota 2 – ale grupa „9” wchodzi w całości (trzy złote medale na pięć osób).
    assert awards == [G, G, G, NONE, NONE]


def test_exclusive_policy_with_a_top_group_bigger_than_the_pool_gives_no_gold():
    params = SchemeParams(gold_percent=Decimal(10), tie_policy=TiePolicy.EXCLUSIVE, hm_percent_of_best=None)
    awards, thresholds = awards_of([5, 5, 5, 1, 1, 1, 1, 1, 1, 1], params)

    assert thresholds.cutoffs[G] is None
    assert G not in awards


def test_zero_never_earns_an_award_even_in_a_tiny_field():
    awards, _ = awards_of([0, 0, 0])

    assert awards == [NONE, NONE, NONE]


def test_disqualified_are_outside_the_field_and_get_nothing():
    totals = [100, 90, 80, 70]
    awards, thresholds = awards_of(totals, statuses={1: StageEntryStatus.DISQUALIFIED})

    assert awards[0] == NONE
    assert thresholds.field_size == 3
    # Pula złota z pola trzech: ceil(3 × 8 %) = 1 – złoto dostaje najlepszy **niezdyskwalifikowany**.
    assert awards[1] == G


def test_honourable_mention_by_percent_of_the_best_score():
    totals = [100] + [90] * 9 + [60, 49, 10]
    params = SchemeParams(
        gold_percent=Decimal(8), silver_percent=Decimal(0), bronze_percent=Decimal(0), hm_full_solution=False
    )
    awards, thresholds = awards_of(totals, params)

    assert thresholds.hm_cutoff == Decimal(50)
    assert awards[0] == G
    assert awards[10] == HM  # 60 ≥ 50 % z 100
    assert awards[11] == NONE  # 49 < 50
    assert awards[12] == NONE


def test_honourable_mention_for_a_full_solution_like_imo():
    totals = [40, 30, 12, 7]
    params = SchemeParams(
        gold_percent=Decimal(25),
        silver_percent=Decimal(0),
        bronze_percent=Decimal(0),
        hm_percent_of_best=None,
    )
    full = {"1": Decimal(7), "2": Decimal(7)}
    awards, _ = awards_of(
        totals,
        params,
        points={3: {"1": Decimal(5), "2": Decimal(7)}, 4: {"1": Decimal(6), "2": Decimal(1)}},
        full_scores=full,
    )

    assert awards[0] == G
    assert awards[2] == HM  # 7/7 w zadaniu 2
    assert awards[3] == NONE  # żadnego zadania na maksimum


def test_honourable_mention_can_be_switched_off_entirely():
    params = SchemeParams(
        gold_percent=Decimal(10),
        silver_percent=Decimal(0),
        bronze_percent=Decimal(0),
        hm_percent_of_best=None,
        hm_full_solution=False,
    )
    awards, _ = awards_of([10, 9, 8], params)

    assert awards == [G, NONE, NONE]


def test_cutoff_with_more_slots_than_positive_scores():
    assert cutoff([Decimal(5), Decimal(3)], 4, TiePolicy.INCLUSIVE) == Decimal(3)
    assert cutoff([], 3, TiePolicy.INCLUSIVE) is None


def test_overrides_beat_the_computed_award():
    assert final_awards({1: G, 2: NONE}, {2: B}) == {1: G, 2: B}


def test_country_table_aggregates_and_ranks_by_total():
    rows = rows_for([30, 20, 25, 5], statuses={4: StageEntryStatus.DISQUALIFIED})
    awards = {1: G, 2: S, 3: HM, 4: NONE}
    countries = {1: ("de", "Germany"), 2: ("de", "Germany"), 3: ("pl", "Poland"), 4: ("pl", "Poland")}

    table = country_table(rows, awards, countries)

    assert [item["code"] for item in table] == ["de", "pl"]
    germany, poland = table
    assert germany["rank"] == 1 and germany["total"] == 50
    assert germany[G] == 1 and germany[S] == 1
    # Zdyskwalifikowany liczy się do uczestników, ale jego punkty nie wchodzą do sumy.
    assert poland["contestants"] == 2 and poland["total"] == 25 and poland[HM] == 1


def test_country_table_shares_rank_on_equal_totals_and_medal_order_is_olympic():
    rows = rows_for([10, 10, 3])
    awards = {1: S, 2: B, 3: G}
    countries = {1: ("a", "Alpha"), 2: ("b", "Beta"), 3: ("c", "Gamma")}

    table = country_table(rows, awards, countries)

    assert [item["rank"] for item in table] == [1, 1, 3]
    assert [item["code"] for item in medal_order(table)] == ["c", "a", "b"]
