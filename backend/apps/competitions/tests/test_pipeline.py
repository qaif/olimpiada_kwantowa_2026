"""Przebieg zawodów jako dane: ``PipelineStep`` i ``TransitionRule`` (§ 1.2.2, § 1.2.5).

Przedmiotem tych testów są **więzy i walidacja** dwóch nowych modeli oraz jedna zmiana przy
istniejącym: unikalne (edycja, rodzaj) obowiązuje od etapu 2 każdy rodzaj poza ``ROUND``.

Czego tu **nie** ma i być nie może: liczenia wyników. Dopóki flaga ``process_editor`` jest
wyłączona, nikt tych wierszy nie czyta – kwalifikację dwudrożną dokłada T30, a dowód, że obie drogi
dają tę samą tabelę, jest w ``apps/tenancy/tests/test_results_snapshot.py`` (T28).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.competitions.models import (
    PipelineStep,
    QualificationMode,
    Stage,
    StageKind,
    TransitionGroupBy,
    TransitionMode,
    TransitionRule,
)

from .factories import CurrentEditionFactory, EditionFactory, StageFactory

pytestmark = pytest.mark.django_db


def make_step(stage, position: int, *, off_pipeline: bool = False) -> PipelineStep:
    """Krok przebiegu dla etapu – bez serwisu, bo serwisu jeszcze nie ma (ekran dokłada T27)."""
    return PipelineStep.objects.create(
        edition_id=stage.edition_id, stage=stage, position=position, off_pipeline=off_pipeline
    )


# --- więz rodzaju etapu: ta sama nazwa, warunek na ``ROUND`` --------------------------------------


def test_the_unique_kind_constraint_keeps_its_name():
    """Nazwa więzi jest częścią kontraktu wdrożenia – zmiana byłaby nowym bytem w logach."""
    names = {constraint.name for constraint in Stage._meta.constraints}

    assert "competitions_stage_unique_kind" in names


def test_two_stages_of_the_same_kind_are_still_rejected():
    """Dla Olimpiady Kwantowej więz działa dosłownie jak przed etapem 2: jedne eliminacje."""
    edition = CurrentEditionFactory()
    StageFactory(edition=edition, kind=StageKind.ELIM)

    with pytest.raises(IntegrityError, match="competitions_stage_unique_kind"), transaction.atomic():
        StageFactory(edition=edition, kind=StageKind.ELIM)


def test_two_rounds_in_one_edition_are_allowed():
    """Rundy rozróżnia nazwa, a nie rodzaj – i tylko dla nich więz jest zawieszony."""
    edition = CurrentEditionFactory()

    first = StageFactory(edition=edition, kind=StageKind.ROUND, name="Runda 1")
    second = StageFactory(edition=edition, kind=StageKind.ROUND, name="Runda 2")

    assert Stage.objects.filter(edition=edition, kind=StageKind.ROUND).count() == 2
    assert first.pk != second.pk


def test_a_round_does_not_block_the_other_kinds():
    """Runda obok eliminacji: warunek zdejmuje więz z ``ROUND``, a nie z reszty rodzajów."""
    edition = CurrentEditionFactory()
    StageFactory(edition=edition, kind=StageKind.ROUND, name="Runda 1")
    StageFactory(edition=edition, kind=StageKind.ELIM)

    with pytest.raises(IntegrityError, match="competitions_stage_unique_kind"), transaction.atomic():
        StageFactory(edition=edition, kind=StageKind.ELIM)


# --- krok przebiegu ------------------------------------------------------------------------------


def test_one_place_in_the_queue_belongs_to_one_step():
    edition = CurrentEditionFactory()
    make_step(StageFactory(edition=edition, kind=StageKind.ELIM), 1)
    district = StageFactory(edition=edition, kind=StageKind.DISTRICT)

    with (
        pytest.raises(IntegrityError, match="competitions_pipelinestep_unique_position"),
        transaction.atomic(),
    ):
        make_step(district, 1)


def test_steps_outside_the_pipeline_share_position_zero():
    """Trening, warsztat i sesja próbna nie mają miejsca w kolejce, więc nie mają o co się bić."""
    edition = CurrentEditionFactory()
    training = StageFactory(edition=edition, kind=StageKind.TRAINING, name="Trening")
    workshop = StageFactory(edition=edition, kind=StageKind.ROUND, name="Warsztat")

    make_step(training, 0, off_pipeline=True)
    make_step(workshop, 0, off_pipeline=True)

    assert PipelineStep.objects.filter(edition=edition, off_pipeline=True).count() == 2


def test_the_same_place_is_free_again_in_another_edition():
    """Więz jest o kolejce **jednej** edycji – rocznik nie ma wpływu na rocznik."""
    stage = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)
    archival = StageFactory(edition=EditionFactory(), kind=StageKind.ELIM)

    make_step(stage, 1)
    make_step(archival, 1)

    assert PipelineStep.objects.filter(position=1).count() == 2


def test_a_step_cannot_point_at_a_stage_of_another_edition():
    """Reguły nie da się zapisać więzem bazy, więc jest jawna w ``clean()`` – i jest sprawdzana."""
    stage = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)
    other_edition = EditionFactory()
    step = PipelineStep(edition=other_edition, stage=stage, position=1)

    with pytest.raises(ValidationError) as error:
        step.full_clean()

    assert "stage" in error.value.message_dict


def test_a_stage_has_at_most_one_step():
    edition = CurrentEditionFactory()
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    make_step(stage, 1)

    with pytest.raises(IntegrityError), transaction.atomic():
        PipelineStep.objects.create(edition=edition, stage=stage, position=2)


def test_steps_are_ordered_by_place_in_the_queue():
    edition = CurrentEditionFactory()
    final = make_step(StageFactory(edition=edition, kind=StageKind.FINAL), 3)
    elim = make_step(StageFactory(edition=edition, kind=StageKind.ELIM), 1)
    district = make_step(StageFactory(edition=edition, kind=StageKind.DISTRICT), 2)

    assert list(PipelineStep.objects.filter(edition=edition)) == [elim, district, final]


# --- reguła przejścia ----------------------------------------------------------------------------


def test_the_four_modes_of_today_map_one_to_one():
    """Cztery dzisiejsze tryby progu mają w nowym modelu dokładnie jeden odpowiednik każdy."""
    expected = {
        QualificationMode.MIN_POINTS: (TransitionMode.MIN_POINTS, TransitionGroupBy.NONE),
        QualificationMode.TOP_N: (TransitionMode.TOP_N, TransitionGroupBy.NONE),
        QualificationMode.TOP_N_PER_DISTRICT: (
            TransitionMode.TOP_N_PER_GROUP,
            TransitionGroupBy.REGION,
        ),
        QualificationMode.HYBRID: (TransitionMode.HYBRID, TransitionGroupBy.NONE),
    }

    assert set(expected) == set(QualificationMode)
    assert {mode for mode, _ in expected.values()} <= set(TransitionMode)


@pytest.mark.parametrize(
    ("mode", "fields", "missing"),
    [
        (TransitionMode.MIN_POINTS, {}, "min_points"),
        (TransitionMode.TOP_N, {}, "top_n"),
        (TransitionMode.TOP_N_PER_GROUP, {"group_by": TransitionGroupBy.REGION}, "top_n"),
        (TransitionMode.HYBRID, {"min_points": 40}, "top_n"),
        (TransitionMode.PERCENTILE, {}, "percentile"),
        (TransitionMode.TOP_N_PER_GROUP, {"top_n": 3}, "group_by"),
    ],
)
def test_a_rule_without_its_parameter_does_not_validate(mode, fields, missing):
    """Ta sama reguła, co w ``QualificationRule.clean`` – próg bez liczby nie jest progiem."""
    step = make_step(StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM), 1)
    rule = TransitionRule(step=step, mode=mode, **fields)

    with pytest.raises(ValidationError) as error:
        rule.full_clean()

    assert missing in error.value.message_dict


def test_the_committee_only_mode_needs_no_parameter():
    """``MANUAL`` znaczy „przechodzi ten, komu komitet wpisał decyzję” – próg byłby tu martwy."""
    step = make_step(StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM), 1)

    TransitionRule(step=step, mode=TransitionMode.MANUAL).full_clean()


def test_a_percentile_outside_the_range_does_not_reach_the_table():
    step = make_step(StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM), 1)

    with (
        pytest.raises(IntegrityError, match="competitions_transitionrule_percentile_range"),
        transaction.atomic(),
    ):
        TransitionRule.objects.create(step=step, mode=TransitionMode.PERCENTILE, percentile=101)


def test_a_zero_top_n_does_not_reach_the_table():
    """Ostatnia linia obrony przed zapisem z pominięciem ``full_clean()`` – „najlepszych zero”."""
    step = make_step(StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM), 1)

    with (
        pytest.raises(IntegrityError, match="competitions_transitionrule_top_n_positive"),
        transaction.atomic(),
    ):
        TransitionRule.objects.create(step=step, mode=TransitionMode.TOP_N, top_n=0)


def test_a_step_takes_several_rules_in_order():
    """„30 najlepszych **oraz** każdy z 90 punktami” to dwa wiersze, a nie jedna sklejka."""
    step = make_step(StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM), 1)
    second = TransitionRule.objects.create(
        step=step, mode=TransitionMode.MIN_POINTS, min_points=90, position=1
    )
    first = TransitionRule.objects.create(step=step, mode=TransitionMode.TOP_N, top_n=30, position=0)

    assert list(step.transition_rules.all()) == [first, second]


# --- zakresowanie konkursem ----------------------------------------------------------------------


def test_steps_and_rules_of_another_competition_are_invisible(competition, other_competition):
    """Konfiguracja przebiegu jednego organizatora nie ma prawa pokazać się drugiemu."""
    mine = make_step(StageFactory(competition=competition, kind=StageKind.ELIM), 1)
    theirs = make_step(StageFactory(competition=other_competition, kind=StageKind.ELIM), 1)
    my_rule = TransitionRule.objects.create(step=mine, mode=TransitionMode.MIN_POINTS, min_points=1)
    TransitionRule.objects.create(step=theirs, mode=TransitionMode.MIN_POINTS, min_points=1)

    assert list(PipelineStep.objects.for_competition(competition)) == [mine]
    assert list(PipelineStep.objects.for_competition(other_competition)) == [theirs]
    assert list(TransitionRule.objects.for_competition(competition)) == [my_rule]
