"""Kryterium 1 T-07: suma po najnowszych wersjach, brak zgłoszenia = 0, niezakończona ocena → 409.

Dodatkowo test liczby zapytań: przeliczenie etapu musi kosztować tyle samo zapytań dla 5 i dla 50
wpisów – inaczej finał z tysiącami prac wywróciłby się na N+1.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.competitions.models import StageEntry
from apps.core.api import DomainError
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.services import compute_stage_results
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db

#: Stany, w których ocena jeszcze trwa – każdy blokuje przeliczenie etapu.
UNFINISHED = [
    SubmissionStatus.IN_REVIEW,
    SubmissionStatus.MODERATION,
    SubmissionStatus.GRADED_PROVISIONAL,
    SubmissionStatus.APPEALED,
]


def test_total_is_sum_of_final_grades_of_latest_versions():
    """1. Liczy się ocena najnowszej wersji, a nie suma wszystkich wersji."""
    stage = make_stage(problems=2)
    first, second = stage_problems(stage)
    entry = graded_entry(stage, [6, 2])
    # Druga wersja pierwszego zadania: poprawka oceniona niżej – to ona się liczy.
    newer = SubmissionFactory(entry=entry, problem=first, version=2, status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=newer, score=5)

    rows = compute_stage_results(stage)

    assert len(rows) == 1
    assert rows[0]["points"] == {str(first.number): 5, str(second.number): 2}
    assert rows[0]["total"] == 7
    entry.refresh_from_db()
    assert entry.total_points == 7


def test_missing_submission_counts_as_zero():
    """1. Brak zgłoszenia do zadania = 0 punktów, a nie brak wiersza w tabeli."""
    stage = make_stage(problems=3)
    first, second, third = stage_problems(stage)
    graded_entry(stage, [6, None, None])

    rows = compute_stage_results(stage)

    assert rows[0]["points"] == {
        str(first.number): 6,
        str(second.number): 0,
        str(third.number): 0,
    }
    assert rows[0]["total"] == 6


def test_infected_version_is_skipped_and_previous_one_counts():
    """1. Wersja odrzucona przez antywirusa nie liczy się – punktuje ostatnia nieodrzucona."""
    stage = make_stage(problems=1)
    (problem,) = stage_problems(stage)
    entry = graded_entry(stage, [5])
    SubmissionFactory(entry=entry, problem=problem, version=2, status=SubmissionStatus.REJECTED_INFECTED)

    rows = compute_stage_results(stage)

    assert rows[0]["total"] == 5


@pytest.mark.parametrize("unfinished_status", UNFINISHED)
def test_unfinished_grading_blocks_computation(unfinished_status):
    """1. Zgłoszenie w ocenie → 409 STAGE_NOT_FINALIZED z pseudonimem pracy do dokończenia."""
    stage = make_stage(problems=1)
    (problem,) = stage_problems(stage)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=problem, status=unfinished_status)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"
    assert error.value.status_code == 409
    assert entry.participant.public_code in str(error.value.detail)
    assert error.value.public_codes == [entry.participant.public_code]


def test_submitted_without_final_grade_blocks_computation():
    """1. Praca sprzed oceniania (SUBMITTED/LOCKED/SCANNING) bez oceny też blokuje przeliczenie."""
    stage = make_stage(problems=1)
    (problem,) = stage_problems(stage)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.LOCKED)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"


def test_final_submission_without_a_final_grade_blocks_computation():
    """6 (przegląd). Praca w stanie FINAL, ale bez ``FinalGrade``, to brak oceny, a nie zero.

    Bez tej blokady nieoceniona praca cicho wpadałaby do tabeli jako 0 punktów i mogła wyrzucić
    uczestnika spod progu kwalifikacji.
    """
    stage = make_stage(problems=1)
    (problem,) = stage_problems(stage)
    entry = graded_entry(stage, [None])
    SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.FINAL)

    with pytest.raises(DomainError) as error:
        compute_stage_results(stage)

    assert error.value.machine_code == "STAGE_NOT_FINALIZED"
    assert error.value.public_codes == [entry.participant.public_code]


def test_ranking_gives_the_same_place_to_a_tie():
    """Ranking: malejąco po sumie, remis = to samo miejsce (1, 1, 3)."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    graded_entry(stage, [6])
    graded_entry(stage, [2])

    rows = compute_stage_results(stage)

    assert [row["rank"] for row in rows] == [1, 1, 3]
    assert [row["total"] for row in rows] == [6, 6, 2]


def _seed_entries(stage, count: int) -> None:
    for _ in range(count):
        graded_entry(stage, [6, 2])


def test_compute_query_count_does_not_grow_with_entries():
    """Brak N+1: 5 i 50 wpisów kosztuje tyle samo zapytań."""
    small = make_stage(problems=2)
    _seed_entries(small, 5)
    large = make_stage(problems=2)
    _seed_entries(large, 50)

    with CaptureQueriesContext(connection) as small_queries:
        compute_stage_results(small)
    with CaptureQueriesContext(connection) as large_queries:
        compute_stage_results(large)

    assert len(large_queries) == len(small_queries)
    # Zadania, wpisy, zgłoszenia i jeden bulk_update – stały, mały zbiór zapytań.
    assert len(large_queries) <= 6
    assert StageEntry.objects.filter(stage=large, total_points=8).count() == 50
