"""Sumowanie po komponentach etapu (T35, ``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3).

Dwa pytania i oba trzeba zadać w tej kolejności:

1. **Czy etap bez komponentów zachowuje się dokładnie jak dziś?** Czyta ``Stage.format``, sumuje
   oceny zadań bez wag, a dla ``format=QUIZ`` bierze punkty z testu **zamiast** sumy z zadań.
   To jest wymaganie nadrzędne (§ 0.1) i dlatego stoi w tym pliku pierwsze.
2. Czy etap z komponentami liczy ważoną sumę źródeł – i czy waga ``1/1`` daje liczbę **identyczną**
   z dzisiejszym sumowaniem liczb całkowitych (§ 1.2.6: ``Fraction``, zaokrąglenie raz, na końcu).

Komponenty wchodzą za flagą ``process_editor``, bo to ona włącza cały edytor przebiegu (§ 0.6).
Sam wiersz ``StageComponent`` przy wyłączonej fladze jest więc martwym zapisem – i to też jest tu
sprawdzane, bo inaczej migracja albo import mogłyby zmienić ogłoszoną tabelę bez ani jednej decyzji
organizatora.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from apps.competitions.models import ComponentKind, StageComponent, StageFormat
from apps.core.api import DomainError
from apps.results.services import (
    PROCESS_EDITOR_FLAG,
    _round_half_up,
    compute_stage_results,
)
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def add_component(stage, kind, *, position: int = 1, numerator: int = 1, denominator: int = 1, **kwargs):
    return StageComponent.objects.create(
        stage=stage,
        kind=kind,
        position=position,
        weight_numerator=numerator,
        weight_denominator=denominator,
        **kwargs,
    )


def totals(stage) -> list[int]:
    return [row["total"] for row in compute_stage_results(stage)]


# --- (a) etap bez komponentów: ani jednej zmiany --------------------------------------------------


def test_a_stage_without_components_sums_grades_like_today(competition):
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5])
    graded_entry(stage, [2, 0])

    assert totals(stage) == [11, 2]


def test_a_stage_without_components_has_no_component_key(competition):
    """Klucz ``components`` pojawia się wyłącznie tam, gdzie komponenty naprawdę są."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])

    assert "components" not in compute_stage_results(stage)[0]


def test_an_unfinished_paper_still_blocks_the_table(competition):
    """``STAGE_NOT_FINALIZED`` zostaje bramą etapu bez komponentów – bez zmiany kodu i komunikatu."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=stage_problems(stage)[0], status=SubmissionStatus.IN_REVIEW)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"


def test_components_are_a_dead_record_while_the_flag_is_off(competition):
    """Wiersz bez flagi niczego nie liczy: tabela ma wyjść taka, jak przed jego wpisaniem."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5])
    add_component(stage, ComponentKind.SUBMISSIONS, numerator=1, denominator=2)

    assert totals(stage) == [11]
    assert "components" not in compute_stage_results(stage)[0]


# --- (b) waga 1/1: ta sama liczba, co dziś --------------------------------------------------------


def test_weight_one_over_one_equals_the_integer_sum(competition):
    """Najważniejsza asercja tego pliku: włączenie komponentów nie zmienia żadnej sumy."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 5])
    graded_entry(stage, [2, 2])
    graded_entry(stage, [0, 0])
    before = totals(stage)
    add_component(stage, ComponentKind.SUBMISSIONS)
    enable(competition)

    assert totals(stage) == before


def test_rounding_is_half_up_and_happens_once():
    """``Fraction`` w sumie, zaokrąglenie na końcu – ta sama metoda, co w ``apps.quiz.services``."""
    assert _round_half_up(Fraction(1, 2)) == 1
    assert _round_half_up(Fraction(1, 3)) == 0
    assert _round_half_up(Fraction(5, 2)) == 3
    # Trzy trzecie dodane jako ułamki dają dokładnie 1, a nie 0,999… – o to w tym chodzi.
    assert _round_half_up(Fraction(1, 3) + Fraction(1, 3) + Fraction(1, 3)) == 1


# --- (c) kilka form w jednym etapie ---------------------------------------------------------------


def test_two_components_are_added_with_their_weights(competition):
    """Etap o dwóch formach naraz – stan, którego przed etapem 2 nie dało się wyrazić."""
    stage = make_stage(problems=2)
    graded_entry(stage, [6, 6])  # 12 punktów z zadań
    add_component(stage, ComponentKind.SUBMISSIONS, position=1, numerator=1, denominator=2)
    add_component(stage, ComponentKind.ONSITE, position=2, numerator=1, denominator=4)
    enable(competition)

    # 12 * 1/2 + 12 * 1/4 = 9
    assert totals(stage) == [9]


def test_the_row_carries_the_score_of_every_component(competition):
    """Rozbicie na komponenty jest w wierszu roboczym – dla podglądu i dla remisów (T40)."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    written = add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    onsite = add_component(stage, ComponentKind.ONSITE, position=2)
    enable(competition)

    row = compute_stage_results(stage)[0]

    assert row["components"] == {str(written.pk): 6, str(onsite.pk): 6}


def test_a_quiz_component_in_a_written_stage_scores_zero_without_a_quiz(competition):
    """Komponent testowy w etapie bez testu daje zero, a nie wywraca przeliczenia."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    add_component(stage, ComponentKind.QUIZ, position=2)
    enable(competition)

    assert stage.format == StageFormat.SUBMISSIONS
    assert totals(stage) == [6]


def test_a_written_stage_can_score_a_real_quiz_component(competition):
    """Etap pisemny **i** test online naraz: to jest cała zdolność, którą dokłada § 1.2.3.

    Szew z ``apps.quiz`` jest ten sam, co dotąd (``stage_scores``), tylko pytany bez oglądania się
    na ``Stage.format`` – bo forma etapu przestała być jedna.
    """
    from decimal import Decimal

    from apps.quiz.models import AttemptStatus
    from apps.quiz.tests.factories import QuizAttemptFactory, QuizFactory

    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    quiz = QuizFactory(stage=stage)
    QuizAttemptFactory(
        quiz=quiz, entry=entry, status=AttemptStatus.SUBMITTED, score=Decimal("4"), max_points=Decimal("4")
    )
    add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    add_component(stage, ComponentKind.QUIZ, position=2)
    enable(competition)

    assert totals(stage) == [10]


def test_an_interview_component_reads_its_own_table(competition):
    """Stan przejściowy T35 („rozmowa liczy się jako zero”) domknął T36 – punkty niesie ``InterviewScore``.

    Tutaj zostaje sam szew: komponent rozmowy **bez wyniku** liczy się jako zero, gdy jest
    nieobowiązkowy. Wagi, dwie rozmowy w jednym etapie i brama ``STAGE_NOT_FINALIZED`` dla rozmowy
    wymaganej stoją w ``apps/results/tests/test_interview_component.py``.
    """
    from apps.competitions.interviews import record_interview_score

    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    add_component(stage, ComponentKind.SUBMISSIONS, position=1)
    interview = add_component(stage, ComponentKind.INTERVIEW, position=2)
    waiting = add_component(stage, ComponentKind.INTERVIEW, position=3, required=False)
    enable(competition)
    record_interview_score(entry, interview, 5, actor=None)

    row = compute_stage_results(stage)[0]

    assert row["components"][str(interview.pk)] == 5
    assert row["components"][str(waiting.pk)] == 0
    assert row["total"] == 11


# --- (d) komponent nieobowiązkowy -----------------------------------------------------------------


def test_an_optional_component_does_not_hold_the_table_hostage(competition):
    """``required=False`` liczy brak wyniku jako zero – zawody dodatkowe nie blokują całej tabeli."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=stage_problems(stage)[0], status=SubmissionStatus.IN_REVIEW)
    add_component(stage, ComponentKind.SUBMISSIONS, required=False)
    enable(competition)

    assert totals(stage) == [0]


def test_a_required_component_keeps_todays_gate(competition):
    """``required=True`` odtwarza dzisiejsze ``_assert_finalized`` co do kodu błędu."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=stage_problems(stage)[0], status=SubmissionStatus.IN_REVIEW)
    add_component(stage, ComponentKind.SUBMISSIONS, required=True)
    enable(competition)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"


# --- (e) snapshot i izolacja ----------------------------------------------------------------------


def test_the_snapshot_does_not_leak_the_component_breakdown(competition):
    """Snapshot buduje się z jawnej listy pól, więc nowy klucz wiersza roboczego do niego nie wchodzi."""
    from apps.results.models import Anonymization
    from apps.results.services import build_snapshot

    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    add_component(stage, ComponentKind.SUBMISSIONS)
    enable(competition)

    snapshot = build_snapshot(compute_stage_results(stage), Anonymization.CODE)

    assert "components" not in snapshot[0]


def test_components_are_scoped_to_their_competition(competition, other_competition):
    """Komponent dochodzi do konkursu etapem – zakresowanie musi to widzieć (§ 5.7)."""
    stage = make_stage(problems=1)
    component = add_component(stage, ComponentKind.SUBMISSIONS)

    assert component in StageComponent.objects.for_competition(competition)
    assert not StageComponent.objects.for_competition(other_competition).exists()
