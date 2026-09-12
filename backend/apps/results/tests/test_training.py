"""Wyniki etapu treningowego: liczą się i publikują, ale nikogo nie kwalifikują.

Piaskownica ma przejść **całą** ścieżkę zawodów, więc przeliczenie i publikacja muszą w niej
działać. Dwie rzeczy są w niej inne i obie są tu sprawdzone: brama okna reklamacji nie czeka na
wartownika z 2099 roku, a kwalifikacja nie ma gdzie nikogo przenieść.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import TRAINING_DEADLINE, StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)
from apps.results.models import Anonymization
from apps.results.services import apply_qualification, compute_stage_results, publish_results

from .conftest import graded_entry

pytestmark = pytest.mark.django_db


@pytest.fixture
def training_stage():
    """Etap treningowy z dwoma zadaniami. Okno reklamacji stoi na wartowniku, czyli jest otwarte."""
    edition = CurrentEditionFactory()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        opens_at=timezone.now() - timedelta(days=1),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
    )
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=6)
    for number in (1, 2):
        ProblemFactory(stage=stage, number=number, title=f"Zadanie treningowe {number}")
    return stage


def test_compute_sums_points_in_training_stage(training_stage):
    graded_entry(training_stage, [6, 5], public_code="OLM-TRAIN1")

    rows = compute_stage_results(training_stage)

    assert [row["total"] for row in rows] == [11]


def test_qualification_runs_without_waiting_for_the_sentinel_appeal_window(training_stage):
    """W zawodach dałoby to ``APPEAL_WINDOW_OPEN`` – w treningu okno reklamacji to data-wartownik."""
    graded_entry(training_stage, [6, 5], public_code="OLM-TRAIN2")

    summary = apply_qualification(training_stage)

    assert summary["qualified"] == 1
    # Trening nie ma następnego etapu, więc nie ma też gdzie utworzyć wpisu.
    assert summary["next_stage_id"] is None
    assert summary["created_entries"] == 0


def test_publish_freezes_a_training_table(training_stage):
    graded_entry(training_stage, [6, 6], public_code="OLM-TRAIN3")
    graded_entry(training_stage, [0, 2], public_code="OLM-TRAIN4")

    publication = publish_results(training_stage, None, Anonymization.CODE)
    training_stage.refresh_from_db()

    assert training_stage.results_published_at is not None
    assert [row["total"] for row in publication.rows] == [12, 2]
    assert [row["display"] for row in publication.rows] == ["OLM-TRAIN3", "OLM-TRAIN4"]
    statuses = set(training_stage.entries.values_list("status", flat=True))
    assert statuses == {StageEntryStatus.QUALIFIED, StageEntryStatus.NOT_QUALIFIED}


def test_full_names_are_never_published_in_training(training_stage):
    """Nazwiska wolno ogłaszać wyłącznie w finale – piaskownica tego nie obchodzi."""
    from apps.core.api import DomainError

    graded_entry(training_stage, [6, 6], public_code="OLM-TRAIN5")

    with pytest.raises(DomainError) as exc:
        publish_results(training_stage, None, Anonymization.FULL)

    assert exc.value.machine_code == "ANONYMIZATION_NOT_ALLOWED_FOR_STAGE"
