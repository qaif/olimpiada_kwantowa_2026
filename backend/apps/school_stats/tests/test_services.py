"""Reguły STAT-01 na poziomie serwisu: publikacja, tryby listy, próg k-anonimowości, pamięć podręczna."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.models import Voivodeship
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import EditionFactory, StageFactory
from apps.school_stats import services
from apps.school_stats.services import K_ANONYMITY, Aggregate, visible_for_supervisor
from apps.schools.tests.factories import SchoolFactory

from .conftest import make_supervisor, publish, student

pytestmark = pytest.mark.django_db


def agg(entries: int, scored: int | None = None) -> Aggregate:
    return Aggregate(entries=entries, scored=entries if scored is None else scored)


# --- próg k-anonimowości ----------------------------------------------------------------------


def test_threshold_hides_groups_below_k():
    assert not visible_for_supervisor(agg(K_ANONYMITY - 1), agg(0))
    assert visible_for_supervisor(agg(K_ANONYMITY), agg(0))


@pytest.mark.parametrize(
    ("group", "mine", "visible"),
    [
        (6, 5, False),  # jeden „obcy” – średnia zdradzałaby jego wynik
        (9, 5, False),  # czterech obcych – nadal poniżej progu
        (10, 5, True),  # pięciu obcych – wolno
        (6, 6, True),  # sami uczniowie opiekuna – nic nowego
    ],
)
def test_complement_rule_closes_differencing(group, mine, visible):
    assert visible_for_supervisor(agg(group), agg(mine)) is visible


def test_complement_rule_also_applies_to_scored_entries():
    """Średnia liczy się z wpisów ocenionych – ich dopełnienie też musi przejść próg."""
    assert not visible_for_supervisor(agg(12, scored=6), agg(5, scored=5))


# --- opiekun: publikacja i tryby listy --------------------------------------------------------


def cells_of(data, participant):
    return next(row["cells"] for row in data["table"] if row["participant"].pk == participant.pk)


def test_no_points_or_qualification_before_publication(competition, edition, stage, school, supervisor):
    entry = student(stage, school=school, mine=True, status=StageEntryStatus.QUALIFIED)
    entry.total_points = Decimal(17)
    entry.save(update_fields=["total_points"])

    data = services.supervisor_statistics(supervisor, competition, edition)
    (cell,) = cells_of(data, entry.participant)

    assert cell.registered and cell.submitted and cell.on_time
    assert cell.published is False
    assert cell.total is None and cell.qualified is None
    assert data["comparisons"] == []


def test_points_come_from_the_frozen_publication(competition, edition, stage, school, supervisor):
    """Bieżąca suma po decyzji komisji nie przepisuje ogłoszonej liczby."""
    entry = student(stage, school=school, mine=True, status=StageEntryStatus.QUALIFIED)
    publish(stage, {entry: 12})
    entry.total_points = Decimal(99)
    entry.save(update_fields=["total_points"])

    (cell,) = cells_of(services.supervisor_statistics(supervisor, competition, edition), entry.participant)

    assert cell.published and cell.qualified
    assert cell.total == Decimal(12)


def test_late_submission_is_not_on_time(competition, edition, stage, school, supervisor):
    entry = student(stage, school=school, mine=True, late=True)
    missing = student(stage, school=school, mine=True, submitted=False)

    data = services.supervisor_statistics(supervisor, competition, edition)

    (late_cell,) = cells_of(data, entry.participant)
    (missing_cell,) = cells_of(data, missing.participant)
    assert late_cell.submitted is True and late_cell.on_time is False
    assert missing_cell.submitted is False


def test_qualified_only_list_hides_means_and_points_of_students_off_the_list(
    competition, edition, stage, school, supervisor
):
    on_list = student(stage, school=school, mine=True, status=StageEntryStatus.QUALIFIED)
    off_list = student(stage, school=school, mine=True, status=StageEntryStatus.NOT_QUALIFIED)
    publish(stage, {on_list: 20, off_list: 3}, qualified_only=True)

    data = services.supervisor_statistics(supervisor, competition, edition)

    (on_cell,) = cells_of(data, on_list.participant)
    (off_cell,) = cells_of(data, off_list.participant)
    assert on_cell.total == Decimal(20)
    assert off_cell.off_list is True and off_cell.total is None
    (comparison,) = data["comparisons"]
    assert all(line["aggregate"].mean is None for line in comparison["lines"])


def test_supervisor_sees_only_students_who_chose_them(competition, edition, stage, school, supervisor):
    mine = student(stage, school=school, mine=True)
    other = student(stage, school=school, mine=False)

    data = services.supervisor_statistics(supervisor, competition, edition)

    participants = {row["participant"].pk for row in data["table"]}
    assert mine.participant.pk in participants
    assert other.participant.pk not in participants


def test_school_comparison_requires_verification(competition, edition, stage, school):
    unverified = make_supervisor(competition, school=school, verified=False)
    for _ in range(6):
        student(stage, school=school)
    publish(stage, {})

    data = services.supervisor_statistics(unverified, competition, edition)

    assert data["scope"]["school_key"] is None
    keys = [line["key"] for line in data["comparisons"][0]["lines"]]
    assert "school" not in keys
    assert "region" in keys and "all" in keys


def test_school_mean_hidden_when_it_would_reveal_one_other_student(
    competition, edition, stage, school, supervisor
):
    mine = [student(stage, school=school, mine=True) for _ in range(5)]
    other = student(stage, school=school)
    publish(stage, {**{entry: 10 for entry in mine}, other: 2})

    data = services.supervisor_statistics(supervisor, competition, edition)
    lines = {line["key"]: line for line in data["comparisons"][0]["lines"]}

    assert lines["mine"]["visible"] and lines["mine"]["aggregate"].mean == Decimal(10)
    assert lines["school"]["visible"] is False
    assert lines["all"]["visible"] is False


def test_school_mean_shown_with_enough_other_students(competition, edition, stage, school, supervisor):
    mine = [student(stage, school=school, mine=True) for _ in range(5)]
    others = [student(stage, school=school) for _ in range(5)]
    publish(stage, {**{entry: 10 for entry in mine}, **{entry: 4 for entry in others}})

    data = services.supervisor_statistics(supervisor, competition, edition)
    lines = {line["key"]: line for line in data["comparisons"][0]["lines"]}

    assert lines["school"]["visible"]
    assert lines["school"]["aggregate"].mean == Decimal(7)
    assert lines["school"]["aggregate"].entries == 10


def test_progress_has_a_point_per_edition(competition, school, supervisor):
    old = EditionFactory(competition=competition, year_label="2025/2026")
    old_stage = StageFactory(edition=old, kind=StageKind.ELIM)
    entries = [student(old_stage, school=school, mine=True) for _ in range(2)]
    publish(old_stage, {entries[0]: 8, entries[1]: 4})

    data = services.supervisor_statistics(supervisor, competition, old)

    assert data["progress"]["labels"] == ["2025/2026"]
    assert data["progress"]["series"]["mine"] == [Decimal(6)]
    # Dwie osoby w całej olimpiadzie – pod progiem, więc punkt „wszyscy” jest pusty, a nie zerem.
    assert data["progress"]["series"]["all"] == [None]


def test_training_stages_are_not_counted(competition, edition, stage, school, supervisor):
    training = StageFactory(edition=edition, kind=StageKind.TRAINING)
    student(training, school=school, mine=True)

    summary = services.edition_summary(edition)

    assert [item.stage_id for item in summary.stages] == [stage.pk]


# --- koordynator --------------------------------------------------------------------------------


def test_coordinator_cell_masks_results_but_not_participation(
    competition, edition, stage, school, other_school
):
    big = [student(stage, school=school, status=StageEntryStatus.QUALIFIED) for _ in range(5)]
    small = [student(stage, school=other_school) for _ in range(2)]
    publish(stage, {**{entry: 6 for entry in big}, **{entry: 1 for entry in small}})

    data = services.coordinator_statistics(edition)
    rows = {row["group"].school_id: row for row in data["ranking"]}

    assert rows[school.pk]["cells"][0] == {"entries": 5, "hidden": False, "mean": Decimal(6), "qualified": 5}
    assert rows[other_school.pk]["participants"] == 2
    assert rows[other_school.pk]["cells"][0]["mean"] is None
    assert rows[other_school.pk]["cells"][0]["hidden"] is True


def test_ranking_compares_with_previous_edition_and_lists_lost_schools(competition, school, other_school):
    previous = EditionFactory(competition=competition, year_label="2025/2026")
    previous_stage = StageFactory(edition=previous)
    current = EditionFactory(competition=competition, year_label="2026/2027")
    current_stage = StageFactory(edition=current)
    student(previous_stage, school=school)
    student(previous_stage, school=other_school)
    for _ in range(3):
        student(current_stage, school=school)

    data = services.coordinator_statistics(current)

    (row,) = data["ranking"]
    assert row["participants"] == 3 and row["previous"] == 1 and row["change"] == 2
    assert [info.school_id for info in data["lost"]] == [other_school.pk]


def test_free_text_schools_group_by_folded_name(competition, edition, stage):
    student(stage, school_text="Liceum Łódzkie")
    student(stage, school_text="  liceum lodzkie ")

    summary = services.edition_summary(edition)

    (group,) = summary.groups.values()
    assert group.participants == 2 and group.school_id is None


def test_ranking_dataset_has_no_personal_columns(competition, edition, stage, school):
    entry = student(stage, school=school)
    publish(stage, {entry: 5})

    dataset = services.ranking_dataset(edition)
    rows = list(dataset.rows)

    assert dataset.count == 1
    assert rows[0][0] == school.name
    flat = " ".join(str(value) for value in rows[0])
    assert entry.participant.public_code not in flat
    assert entry.participant.user.last_name not in flat


def test_region_table_counts_participants_per_voivodeship(competition, edition, stage, school, other_school):
    student(stage, school=school)
    student(stage, school=other_school)
    student(stage, school=other_school)

    data = services.coordinator_statistics(edition)

    counts = {item["region"].code: item["region"].participants for item in data["regions"]}
    assert counts == {Voivodeship.MALOPOLSKIE: 2, Voivodeship.MAZOWIECKIE: 1}


# --- wydajność i pamięć podręczna -------------------------------------------------------------


def test_summary_query_count_does_not_grow_with_entries(
    competition, edition, stage, django_assert_max_num_queries
):
    for index in range(12):
        student(stage, school=SchoolFactory() if index % 2 else None)
    publish(stage, {})
    stages = services.competition_stages(edition)

    with django_assert_max_num_queries(2):
        services.build_summary(edition, stages)


def test_summary_cache_is_invalidated_by_publication(
    competition, edition, stage, school, django_assert_num_queries
):
    entries = [student(stage, school=school) for _ in range(5)]
    before = services.edition_summary(edition)
    assert before.stages[0].published is False

    with django_assert_num_queries(1):  # tylko etapy – reszta z pamięci podręcznej
        services.edition_summary(edition)

    publish(stage, dict.fromkeys(entries, 3))
    after = services.edition_summary(edition)

    assert after.stages[0].published is True
    assert after.stages[0].all.mean == Decimal(3)
