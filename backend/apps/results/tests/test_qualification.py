"""Kryteria 2 i 3 T-07: cztery tryby progu, remisy na granicy i wpisy w następnym etapie."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.competitions.models import (
    InterviewBooking,
    QualificationMode,
    StageEntry,
    StageEntryStatus,
    StageKind,
)
from apps.competitions.tests.factories import EditionFactory, InterviewSlotFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import FinalGrade
from apps.results.services import apply_qualification
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db

QUALIFIED = StageEntryStatus.QUALIFIED
NOT_QUALIFIED = StageEntryStatus.NOT_QUALIFIED
DISQUALIFIED = StageEntryStatus.DISQUALIFIED


def statuses(entries: list[StageEntry]) -> list[str]:
    for entry in entries:
        entry.refresh_from_db()
    return [entry.status for entry in entries]


def test_min_points_mode():
    """2. MIN_POINTS: próg punktowy; zdyskwalifikowany zostaje nietknięty."""
    stage = make_stage(problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    above = graded_entry(stage, [6])
    exactly = graded_entry(stage, [5])
    below = graded_entry(stage, [2])
    disqualified = graded_entry(stage, [6], status=DISQUALIFIED)

    apply_qualification(stage)

    assert statuses([above, exactly, below, disqualified]) == [
        QUALIFIED,
        QUALIFIED,
        NOT_QUALIFIED,
        DISQUALIFIED,
    ]


def test_top_n_mode_admits_the_whole_tie_at_the_cutoff():
    """2. TOP_N: przy remisie na granicy wchodzą wszyscy z tym samym wynikiem (tu 3 przy N=2)."""
    stage = make_stage(problems=1, mode=QualificationMode.TOP_N, min_points=None, top_n=2)
    best = graded_entry(stage, [6])
    tie_one = graded_entry(stage, [5])
    tie_two = graded_entry(stage, [5])
    worst = graded_entry(stage, [2])

    apply_qualification(stage)

    assert statuses([best, tie_one, tie_two, worst]) == [
        QUALIFIED,
        QUALIFIED,
        QUALIFIED,
        NOT_QUALIFIED,
    ]


def test_top_n_per_district_mode():
    """2. TOP_N_PER_DISTRICT: próg liczy się osobno w każdym okręgu, remisy jak wyżej."""
    stage = make_stage(problems=1, mode=QualificationMode.TOP_N_PER_DISTRICT, min_points=None, top_n=1)
    north_tie_one = graded_entry(stage, [6], district="pomorski")
    north_tie_two = graded_entry(stage, [6], district="pomorski")
    north_worst = graded_entry(stage, [2], district="pomorski")
    # Okręg słabszy punktowo, ale i tak wprowadza swojego najlepszego uczestnika.
    south_best = graded_entry(stage, [5], district="malopolski")
    south_worst = graded_entry(stage, [0], district="malopolski")

    apply_qualification(stage)

    assert statuses([north_tie_one, north_tie_two, north_worst, south_best, south_worst]) == [
        QUALIFIED,
        QUALIFIED,
        NOT_QUALIFIED,
        QUALIFIED,
        NOT_QUALIFIED,
    ]


def test_hybrid_mode_requires_both_conditions():
    """2. HYBRID: trzeba być w top N **i** mieć minimum punktów."""
    stage = make_stage(problems=1, mode=QualificationMode.HYBRID, min_points=5, top_n=3)
    best = graded_entry(stage, [6])
    at_min = graded_entry(stage, [5])
    in_top_but_below_min = graded_entry(stage, [2])
    out_of_top = graded_entry(stage, [2])

    apply_qualification(stage)

    assert statuses([best, at_min, in_top_but_below_min, out_of_top]) == [
        QUALIFIED,
        QUALIFIED,
        NOT_QUALIFIED,
        NOT_QUALIFIED,
    ]


def test_top_n_never_admits_entries_without_a_single_point():
    """2. „N najlepszych” spośród samych zer to nikt – brak punktów nie jest wynikiem."""
    stage = make_stage(problems=1, mode=QualificationMode.TOP_N, min_points=None, top_n=2)
    entries = [graded_entry(stage, [0]) for _ in range(5)]

    summary = apply_qualification(stage)

    assert summary["qualified"] == 0
    assert statuses(entries) == [NOT_QUALIFIED] * 5


def test_top_n_larger_than_the_field_admits_only_those_with_points():
    """2. N większe niż liczba punktujących: wchodzą wszyscy punktujący i nikt więcej."""
    stage = make_stage(problems=1, mode=QualificationMode.TOP_N, min_points=None, top_n=5)
    scoring = [graded_entry(stage, [score]) for score in (6, 5, 2)]
    empty = [graded_entry(stage, [0]) for _ in range(7)]

    summary = apply_qualification(stage)

    assert summary["qualified"] == 3
    assert statuses(scoring) == [QUALIFIED] * 3
    assert statuses(empty) == [NOT_QUALIFIED] * 7


def test_hybrid_with_a_zero_minimum_still_ignores_zero_scores():
    """2. HYBRID z ``min_points=0`` nie może przepuścić prac bez ani jednego punktu."""
    stage = make_stage(problems=1, mode=QualificationMode.HYBRID, min_points=0, top_n=3)
    scoring = graded_entry(stage, [2])
    empty = graded_entry(stage, [0])

    apply_qualification(stage)

    assert statuses([scoring, empty]) == [QUALIFIED, NOT_QUALIFIED]


def test_qualification_before_the_appeal_window_closes_is_rejected():
    """4 (przegląd). Próg liczymy po zamknięciu reklamacji, nigdy wcześniej – 409."""
    stage = make_stage(problems=1, appeals_open=True)

    with pytest.raises(DomainError) as error:
        apply_qualification(stage)

    assert error.value.machine_code == "APPEAL_WINDOW_OPEN"
    assert error.value.status_code == 409


def test_qualified_entries_are_created_in_the_next_stage_once():
    """3. Wpis w następnym etapie tylko dla QUALIFIED; drugie wywołanie nic nie duplikuje."""
    edition = EditionFactory()
    elimination = make_stage(edition=edition, problems=1, mode=QualificationMode.MIN_POINTS, min_points=5)
    district = make_stage(edition=edition, kind=StageKind.DISTRICT, problems=1)
    passing = graded_entry(elimination, [6])
    failing = graded_entry(elimination, [2])

    first = apply_qualification(elimination)
    second = apply_qualification(elimination)

    assert first["created_entries"] == 1
    assert second["created_entries"] == 0
    assert first["next_stage_id"] == district.pk
    next_entries = list(StageEntry.objects.filter(stage=district))
    assert len(next_entries) == 1
    assert next_entries[0].participant_id == passing.participant_id
    assert next_entries[0].status == StageEntryStatus.REGISTERED
    assert not StageEntry.objects.filter(stage=district, participant=failing.participant).exists()


def elimination_with_district(min_points: int = 5):
    """Para etapów jednej edycji: eliminacje z progiem i etap okręgowy jako następny."""
    edition = EditionFactory()
    elimination = make_stage(
        edition=edition, problems=1, mode=QualificationMode.MIN_POINTS, min_points=min_points
    )
    district = make_stage(edition=edition, kind=StageKind.DISTRICT, problems=1)
    return elimination, district


def regrade(entry, score: int) -> None:
    """Zmienia ocenę uzgodnioną – tak jak decyzja komisji odwoławczej po reklamacji."""
    grade = FinalGrade.objects.get(submission__entry=entry)
    grade.score = score
    grade.save(update_fields=["score"])


def test_losing_qualification_removes_the_empty_entry_in_the_next_stage():
    """3 (przegląd). Uczestnik, który spadł poniżej progu, znika z pustego wpisu w kolejnym etapie."""
    elimination, district = elimination_with_district()
    entry = graded_entry(elimination, [6])
    apply_qualification(elimination)
    assert StageEntry.objects.filter(stage=district, participant=entry.participant).exists()

    regrade(entry, 2)
    summary = apply_qualification(elimination)

    assert statuses([entry]) == [NOT_QUALIFIED]
    assert summary["removed_entries"] == 1
    assert summary["next_stage_conflicts"] == []
    assert not StageEntry.objects.filter(stage=district, participant=entry.participant).exists()
    diff = AuditLog.objects.filter(action="results.qualification_applied").order_by("id").last().diff
    assert diff["removed_entries"] == 1
    assert diff["next_stage_conflicts"] == 0


def test_next_stage_entry_with_a_submission_is_kept_and_reported_as_a_conflict():
    """3 (przegląd). Wpisu z oddaną pracą serwis nie kasuje – zgłasza go koordynatorowi."""
    elimination, district = elimination_with_district()
    entry = graded_entry(elimination, [6])
    apply_qualification(elimination)
    next_entry = StageEntry.objects.get(stage=district, participant=entry.participant)
    SubmissionFactory(
        entry=next_entry, problem=stage_problems(district)[0], status=SubmissionStatus.SUBMITTED
    )

    regrade(entry, 2)
    summary = apply_qualification(elimination)

    assert summary["removed_entries"] == 0
    assert summary["next_stage_conflicts"] == [entry.participant.public_code]
    assert StageEntry.objects.filter(pk=next_entry.pk).exists()


def test_next_stage_entry_with_an_interview_booking_is_kept_and_reported_as_a_conflict():
    """3 (przegląd). Zapis na rozmowę waży tyle samo, co oddana praca.

    Uczestnik dostał mailem potwierdzenie godziny, a komisja ma go w kalendarzu: ciche skasowanie
    wpisu po ponownym przeliczeniu zabrałoby jedno i drugie. Dodatkowo ``InterviewBooking.slot``
    jest z ``PROTECT``, więc kasowanie i tak wywróciłoby całą kwalifikację na ``ProtectedError``.
    """
    elimination, district = elimination_with_district()
    entry = graded_entry(elimination, [6])
    apply_qualification(elimination)
    next_entry = StageEntry.objects.get(stage=district, participant=entry.participant)
    booking = InterviewBooking.objects.create(slot=InterviewSlotFactory(stage=district), entry=next_entry)

    regrade(entry, 2)
    summary = apply_qualification(elimination)

    assert summary["removed_entries"] == 0
    assert summary["next_stage_conflicts"] == [entry.participant.public_code]
    assert StageEntry.objects.filter(pk=next_entry.pk).exists()
    assert InterviewBooking.objects.filter(pk=booking.pk).exists()


def test_next_stage_entries_cost_a_constant_number_of_queries():
    """3 (przegląd). Wpisy w kolejnym etapie powstają hurtem: 5 i 50 osób to tyle samo zapytań."""
    small, _ = elimination_with_district()
    for _ in range(5):
        graded_entry(small, [6])
    large, _ = elimination_with_district()
    for _ in range(50):
        graded_entry(large, [6])

    with CaptureQueriesContext(connection) as small_queries:
        small_summary = apply_qualification(small)
    with CaptureQueriesContext(connection) as large_queries:
        large_summary = apply_qualification(large)

    assert (small_summary["created_entries"], large_summary["created_entries"]) == (5, 50)
    assert len(large_queries) == len(small_queries)


def test_final_stage_has_no_next_stage():
    """3. Finał nie ma następnego etapu – kwalifikacja tylko ustawia statusy."""
    edition = EditionFactory()
    make_stage(edition=edition, kind=StageKind.ELIM, problems=1)
    final = make_stage(
        edition=edition,
        kind=StageKind.FINAL,
        problems=1,
        mode=QualificationMode.MIN_POINTS,
        min_points=5,
    )
    winner = graded_entry(final, [6])

    summary = apply_qualification(final)

    assert summary["next_stage_id"] is None
    assert summary["created_entries"] == 0
    assert statuses([winner]) == [QUALIFIED]


def test_stage_without_qualification_rule_is_rejected():
    """Bez progu kwalifikacji nie ma czego przeliczać – czytelne 409 zamiast AttributeError."""
    stage = make_stage(problems=1)
    stage.qualification_rule.delete()
    stage.refresh_from_db()

    with pytest.raises(DomainError) as error:
        apply_qualification(stage)

    assert error.value.machine_code == "QUALIFICATION_RULE_MISSING"
    assert error.value.status_code == 409
