"""Fikstury panelu nauczyciela: konkurs z edycją i etapem, nauczyciel, jego klient i uczniowie."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import GROUP_SUPERVISOR, CompetitionRole, SchoolSupervisor
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.tenancy.tests.factories import grant_membership

TEACHER_EMAIL = "nauczycielka@szkola.test"


def flag(competition, name: str, on: bool = True):
    competition.feature_flags = {**(competition.feature_flags or {}), name: on}
    competition.save(update_fields=["feature_flags"])
    return competition


def make_teacher(competition, email: str = TEACHER_EMAIL) -> SchoolSupervisor:
    user = UserFactory(email=email, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    return SchoolSupervisor.objects.create(user=user, competition=competition, school="XIV LO")


def make_student(competition, *, email: str = TEACHER_EMAIL, **kwargs):
    """Uczeń, który wskazał adres nauczyciela (``email``) – tak powstaje uprawnienie opiekuna."""
    kwargs.setdefault("supervisor_email", email)
    return ParticipantFactory(competition=competition, **kwargs)


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition, year_label="2026/2027")


@pytest.fixture
def stage(edition):
    """Etap przyjmujący teraz prace (fabryka: otwarty dobę temu, termin za 14 dni) z dwoma zadaniami."""
    stage = StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I")
    ProblemFactory(stage=stage, number=1)
    ProblemFactory(stage=stage, number=2)
    return stage


@pytest.fixture
def teacher(competition):
    return make_teacher(competition)


@pytest.fixture
def teacher_client(client_for, competition, teacher):
    client = client_for(competition)
    client.force_login(teacher.user)
    return client


def enter(stage, participant, **kwargs):
    return StageEntryFactory(stage=stage, participant=participant, **kwargs)
