"""Wspólne dane testów interfejsu WWW: bieżąca edycja z otwartym etapem eliminacyjnym.

Układ odpowiada temu, co zakłada ``manage.py seed_demo`` (edycja bieżąca, etap ELIM otwarty,
trzy zadania, uczestnicy zapisani do etapu), ale powstaje z fabryk – test widzi dokładnie to,
co zadeklarował, i nie zależy od ``DEBUG``.
"""

import pytest
from django.test import Client

from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)

#: Nazwisko uczestnika użyte w asercjach „recenzent nie widzi danych osobowych”.
PARTICIPANT_LAST_NAME = "Nazwiskowski"


@pytest.fixture
def web_client() -> Client:
    return Client()


@pytest.fixture
def edition():
    return CurrentEditionFactory()


@pytest.fixture
def elim_stage(edition):
    """Otwarty etap eliminacyjny ze skalą 0/2/5/6 i progiem kwalifikacji."""
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


@pytest.fixture
def problems(elim_stage):
    return [
        ProblemFactory(stage=elim_stage, number=1, title="Nierówność ze średnimi"),
        ProblemFactory(stage=elim_stage, number=2, title="Kolorowanie grafu turniejowego"),
    ]


@pytest.fixture
def participant():
    return ParticipantFactory(
        user=UserFactory(
            email="uczestnik@example.test",
            first_name="Uczestnik",
            last_name=PARTICIPANT_LAST_NAME,
            groups=["participant"],
        )
    )


@pytest.fixture
def entry(participant, elim_stage):
    return StageEntryFactory(participant=participant, stage=elim_stage)


@pytest.fixture
def reviewer():
    return ActiveReviewerFactory()


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


def shift_stage(stage, *, opens, deadline, review, appeal_opens, appeal_closes) -> None:
    """Przesuwa oś czasu etapu (dni względem „teraz”), z pominięciem ``full_clean``.

    ``update()`` zamiast ``save()``: w teście interesuje nas stan po deadline, a nie droga do
    niego. Constraintów w bazie (kolejność dat) i tak nie da się tak obejść.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.competitions.models import Stage

    now = timezone.now()
    Stage.objects.filter(pk=stage.pk).update(
        opens_at=now + timedelta(days=opens),
        deadline_at=now + timedelta(days=deadline),
        review_deadline_at=now + timedelta(days=review),
        appeal_window_opens_at=now + timedelta(days=appeal_opens),
        appeal_window_closes_at=now + timedelta(days=appeal_closes),
    )
    stage.refresh_from_db()


def close_submissions(stage) -> None:
    """Etap po deadline uploadu, z otwartym oknem reklamacji."""
    shift_stage(stage, opens=-30, deadline=-2, review=-1, appeal_opens=-1, appeal_closes=7)


def close_stage_timeline(stage) -> None:
    """Etap całkowicie zamknięty: po deadline, po recenzjach i po oknie reklamacji."""
    shift_stage(stage, opens=-60, deadline=-50, review=-40, appeal_opens=-30, appeal_closes=-20)
