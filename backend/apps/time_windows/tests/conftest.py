"""Świat testów okien czasowych: konkurs z flagą, etap z trzema oknami i dwa kraje.

Okna są ustawione **względem teraz** (A trwa, B i C przed startem), a przydział krajów jest
zapisany wierszami ``DelegationWindow`` – przydział domyślny ze strefy zależałby od godziny,
o której biegnie test, więc jego regułę sprawdza osobny test na stałych datach.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.delegations import Delegation
from apps.accounts.models import Region
from apps.accounts.regions import switch_to_countries
from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.tenancy.models import RegistrationMode
from apps.time_windows import services
from apps.time_windows.models import FLAG, DelegationWindow


def enable_flag(competition, value: bool = True):
    competition.feature_flags = {**(competition.feature_flags or {}), FLAG: value}
    competition.save(update_fields=["feature_flags"])
    return competition


@dataclass
class World:
    competition: object
    stage: object
    plan: object
    windows: dict
    problem: object
    delegation_jp: object
    delegation_us: object
    student_a: object
    student_b: object
    entry_a: object
    entry_b: object
    now: object


def build_world(competition, *, now=None) -> World:
    now = (now or timezone.now()).replace(second=0, microsecond=0)
    switch_to_countries(competition)
    competition.registration_mode = RegistrationMode.DELEGATIONS
    competition.save(update_fields=["registration_mode"])
    enable_flag(competition)
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(
        edition=edition, opens_at=now - timedelta(hours=2), deadline_at=now + timedelta(days=2)
    )
    problem = ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"], max_file_mb=1)
    plan = services.create_plan(
        stage,
        duration_minutes=300,
        preferred_local_hour=10,
        first_start=now - timedelta(hours=1),
        count=3,
        interval_minutes=480,
        now=stage.opens_at - timedelta(minutes=5),
    )
    windows = {item.label: item for item in plan.windows.order_by("starts_at")}
    jp = Region.objects.get(competition=competition, code="jp")
    us = Region.objects.get(competition=competition, code="us")
    delegation_jp = Delegation.objects.create(
        competition=competition, edition=edition, country=jp, max_students=6
    )
    delegation_us = Delegation.objects.create(
        competition=competition, edition=edition, country=us, max_students=6
    )
    DelegationWindow.objects.create(plan=plan, delegation=delegation_jp, window=windows["A"])
    DelegationWindow.objects.create(plan=plan, delegation=delegation_us, window=windows["B"])
    student_a = ParticipantFactory(competition=competition, delegation=delegation_jp)
    student_b = ParticipantFactory(competition=competition, delegation=delegation_us)
    entry_a = StageEntryFactory(stage=stage, participant=student_a)
    entry_b = StageEntryFactory(stage=stage, participant=student_b)
    return World(
        competition=competition,
        stage=stage,
        plan=plan,
        windows=windows,
        problem=problem,
        delegation_jp=delegation_jp,
        delegation_us=delegation_us,
        student_a=student_a,
        student_b=student_b,
        entry_a=entry_a,
        entry_b=entry_b,
        now=now,
    )


@pytest.fixture
def world(competition):
    return build_world(competition)


def fresh_stage(stage):
    """Etap z bazy – bez migawki planu zapamiętanej na obiekcie przez wcześniejsze pytanie."""
    from apps.competitions.models import Stage

    return Stage.objects.select_related("edition", "edition__competition").get(pk=stage.pk)
