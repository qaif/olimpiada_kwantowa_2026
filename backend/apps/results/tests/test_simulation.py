"""Symulacja progu kwalifikacji (``apps.results.simulation``) – warstwa danych, bez ekranu.

Testy ekranu leżą w ``apps/web/tests/test_coordinator_simulation.py``. Tutaj sprawdzamy, że
podgląd daje **ten sam** wynik, co przeliczenie (bo korzysta z tej samej funkcji progu), i że
żadne wywołanie symulacji nie dotyka bazy.
"""

import pytest

from apps.accounts.models import Voivodeship
from apps.competitions.models import QualificationMode, QualificationRule, StageEntryStatus
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.results.services import apply_qualification
from apps.results.simulation import apply_rule, build_rule, simulate
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

from .conftest import graded_entry, make_stage, stage_problems

pytestmark = pytest.mark.django_db


def test_build_rule_rejects_an_unknown_mode():
    stage = make_stage()

    with pytest.raises(DomainError) as error:
        build_rule(stage, "NIE_MA_TAKIEGO", None, None)

    assert error.value.machine_code == "QUALIFICATION_RULE_INVALID"


def test_build_rule_rejects_a_mode_without_its_numbers():
    stage = make_stage()

    with pytest.raises(DomainError) as error:
        build_rule(stage, QualificationMode.TOP_N, None, None)

    assert "top_n" in str(error.value.detail)


def test_min_points_cutoff_is_the_lowest_qualifying_total():
    stage = make_stage()
    graded_entry(stage, [6, 6])
    graded_entry(stage, [5, 0])
    graded_entry(stage, [0, 2])

    result = simulate(stage, QualificationMode.MIN_POINTS, 5, None)

    assert result["qualified"] == 2
    assert result["cutoff"] == 5


def test_top_n_per_district_counts_each_voivodeship_separately():
    stage = make_stage()
    graded_entry(stage, [6, 6], district=Voivodeship.MAZOWIECKIE)
    graded_entry(stage, [2, 0], district=Voivodeship.MAZOWIECKIE)
    graded_entry(stage, [2, 0], district=Voivodeship.POMORSKIE)

    result = simulate(stage, QualificationMode.TOP_N_PER_DISTRICT, None, 1)

    by_district = {row["district"]: row for row in result["districts"]}
    assert result["qualified"] == 2
    assert by_district[Voivodeship.MAZOWIECKIE.label]["qualified"] == 1
    assert by_district[Voivodeship.POMORSKIE.label]["entered"] == 1


def test_simulation_agrees_with_the_real_qualification():
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2)
    for scores in ([6, 6], [5, 0], [2, 0]):
        graded_entry(stage, scores)

    preview = simulate(stage, QualificationMode.TOP_N, None, 2)
    applied = apply_qualification(stage)

    assert preview["qualified"] == applied["qualified"]


def test_simulation_counts_work_without_a_grade():
    stage = make_stage()
    entry = graded_entry(stage, [6, None])
    SubmissionFactory(entry=entry, problem=stage_problems(stage)[1], status=SubmissionStatus.IN_REVIEW)

    result = simulate(stage, QualificationMode.MIN_POINTS, 0, None)

    assert result["ungraded"] == 1


def test_simulation_writes_nothing():
    stage = make_stage()
    entry = graded_entry(stage, [6, 6])

    simulate(stage, QualificationMode.TOP_N, None, 1)

    entry.refresh_from_db()
    # Ani statusu, ani sumy punktów: strona symulacji jest żądaniem GET i nie może zmieniać stanu.
    assert entry.status == StageEntryStatus.REGISTERED
    assert entry.total_points is None
    assert not AuditLog.objects.exists()


def test_apply_rule_replaces_the_threshold_and_records_both_sides():
    stage = make_stage(mode=QualificationMode.MIN_POINTS, min_points=3)

    apply_rule(stage, QualificationMode.TOP_N, None, 7)

    rule = QualificationRule.objects.get(stage=stage)
    log = AuditLog.objects.get(action="stage.rule_updated")
    assert (rule.mode, rule.min_points, rule.top_n) == (QualificationMode.TOP_N, None, 7)
    assert log.diff["before"]["min_points"] == 3
    assert log.diff["after"]["top_n"] == 7


def test_apply_rule_leaves_entry_statuses_alone():
    stage = make_stage()
    entry = graded_entry(stage, [6, 6])

    apply_rule(stage, QualificationMode.MIN_POINTS, 1, None)

    entry.refresh_from_db()
    assert entry.status == StageEntryStatus.REGISTERED
