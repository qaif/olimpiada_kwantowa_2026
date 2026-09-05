"""Kryteria 2 i 3 T-07: cztery tryby progu, remisy na granicy i wpisy w następnym etapie."""

import pytest

from apps.competitions.models import (
    QualificationMode,
    StageEntry,
    StageEntryStatus,
    StageKind,
)
from apps.competitions.tests.factories import EditionFactory
from apps.core.api import DomainError
from apps.results.services import apply_qualification

from .conftest import graded_entry, make_stage

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
