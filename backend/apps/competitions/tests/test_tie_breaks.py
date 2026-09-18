"""Rozstrzyganie remisów jako model: więzy, wskazania i zakresowanie (T40, § 1.2.6 c).

Przedmiotem tego pliku jest **wiersz**, a nie miejsce w tabeli: porządek wyników sprawdza
``apps/results/tests/test_tie_breaks.py``, bo tam mieszka jego kod. Tutaj pilnujemy tego, czego
nie widać w ogłoszonej tabeli: że kryterium „wynik we wskazanym zadaniu” nie wejdzie do bazy bez
zadania, że dwa kryteria nie staną na tym samym miejscu w kolejności i że wskazanie należy do tego
samego etapu, co kryterium.

Konkurs #1 nie ma ani jednego kryterium i mieć nie musi – migracja ``0030_tie_break`` tworzy pustą
tabelę i na tym kończy, bo regulamin Olimpiady Kwantowej remisów nie rozstrzyga. To jest pierwsza
asercja tego pliku.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.competitions.models import ComponentKind, StageComponent, TieBreak, TieBreakKey

from .factories import ProblemFactory, StageFactory

pytestmark = pytest.mark.django_db


def make_tie_break(stage, key=TieBreakKey.HIGHEST_SINGLE, **kwargs) -> TieBreak:
    """Kryterium bez fabryki: ``tests/factories.py`` jest plikiem wspólnym kilku zadań naraz."""
    return TieBreak.objects.create(stage=stage, key=key, **kwargs)


# --- Konkurs #1: pusta tabela ---------------------------------------------------------------------


def test_competition_one_has_no_tie_breaks(competition):
    """Migracja tworzy **pustą** tabelę – żaden istniejący etap nie dostaje ani jednego wiersza."""
    assert not TieBreak.objects.for_competition(competition).exists()


# --- kolejność ------------------------------------------------------------------------------------


def test_tie_breaks_come_out_in_the_organiser_order(competition):
    """Kolejność kryteriów jest regulaminem, a nie porządkiem zapisu – rozstrzyga ``position``."""
    stage = StageFactory()
    second = make_tie_break(stage, TieBreakKey.SUBMITTED_AT, position=2, descending=False)
    first = make_tie_break(stage, TieBreakKey.SOLVED_COUNT, position=1)

    assert list(stage.tie_breaks.all()) == [first, second]


def test_two_tie_breaks_cannot_share_a_place_in_one_stage(competition):
    """Dwa kryteria na jednym miejscu dałyby tabelę zależną od porządku wierszy w bazie."""
    stage = StageFactory()
    make_tie_break(stage, position=1)

    with (
        pytest.raises(IntegrityError, match="competitions_tiebreak_unique_position"),
        transaction.atomic(),
    ):
        make_tie_break(stage, TieBreakKey.SOLVED_COUNT, position=1)


# --- wskazania: zadanie i komponent ---------------------------------------------------------------


def test_a_problem_criterion_without_a_problem_is_refused_by_the_database(competition):
    """Kryterium „wynik we wskazanym zadaniu” bez zadania po cichu nie rozstrzygałoby niczego."""
    stage = StageFactory()

    with (
        pytest.raises(IntegrityError, match="competitions_tiebreak_problem_required"),
        transaction.atomic(),
    ):
        make_tie_break(stage, TieBreakKey.PROBLEM_SCORE)


def test_a_component_criterion_without_a_component_is_refused_by_the_database(competition):
    stage = StageFactory()

    with (
        pytest.raises(IntegrityError, match="competitions_tiebreak_component_required"),
        transaction.atomic(),
    ):
        make_tie_break(stage, TieBreakKey.COMPONENT_SCORE)


def test_a_missing_pointer_has_a_readable_message(competition):
    """Ten sam brak, co wyżej, ale w formularzu edytora – koordynator ma czytać zdanie, nie błąd bazy."""
    rule = TieBreak(stage=StageFactory(), key=TieBreakKey.PROBLEM_SCORE)

    with pytest.raises(ValidationError) as error:
        rule.full_clean()

    assert "problem" in error.value.message_dict


def test_a_problem_from_another_stage_is_refused(competition):
    """Zadanie z cudzego etapu jest w tej tabeli wyników liczbą znikąd."""
    stage = StageFactory()
    stranger = ProblemFactory(stage=StageFactory(), number=1)
    rule = TieBreak(stage=stage, key=TieBreakKey.PROBLEM_SCORE, problem=stranger)

    with pytest.raises(ValidationError) as error:
        rule.full_clean()

    assert "Zadanie musi należeć do tego samego etapu." in str(error.value)


def test_a_component_from_another_stage_is_refused(competition):
    stage = StageFactory()
    stranger = StageComponent.objects.create(stage=StageFactory(), kind=ComponentKind.QUIZ)
    rule = TieBreak(stage=stage, key=TieBreakKey.COMPONENT_SCORE, component=stranger)

    with pytest.raises(ValidationError) as error:
        rule.full_clean()

    assert "Komponent musi należeć do tego samego etapu." in str(error.value)


def test_a_pointer_from_the_same_stage_passes(competition):
    """Kryterium ze wskazaniem z własnego etapu przechodzi walidację w całości."""
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1)

    TieBreak(stage=stage, key=TieBreakKey.PROBLEM_SCORE, problem=problem).full_clean()


# --- usuwanie -------------------------------------------------------------------------------------


def test_tie_breaks_disappear_with_their_stage(competition):
    """``CASCADE``: kryterium bez etapu nie jest wierszem do uratowania, tylko śmieciem."""
    stage = StageFactory()
    make_tie_break(stage)
    stage.delete()

    assert not TieBreak.objects.exists()


def test_a_tie_break_disappears_with_the_problem_it_points_at(competition):
    """Kryterium bez zadania nie ma czego liczyć – usunięcie zadania zabiera także regułę."""
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1)
    make_tie_break(stage, TieBreakKey.PROBLEM_SCORE, problem=problem)
    problem.delete()

    assert not TieBreak.objects.exists()


# --- izolacja (§ 5.7) -----------------------------------------------------------------------------


def test_a_tie_break_belongs_to_the_competition_of_its_stage(competition, other_competition):
    """Droga do konkursu wiedzie etapem i edycją – własnej kolumny kryterium nie ma po co mieć."""
    mine = make_tie_break(StageFactory(competition=competition))
    theirs = make_tie_break(StageFactory(competition=other_competition))

    assert list(TieBreak.objects.for_competition(competition)) == [mine]
    assert list(TieBreak.objects.for_competition(other_competition)) == [theirs]
