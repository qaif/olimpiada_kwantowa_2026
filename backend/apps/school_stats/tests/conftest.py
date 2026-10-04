"""Fikstury statystyk szkół: włączona flaga, edycja z etapem, szkoły, opiekun i publikacja.

Publikację zakładamy **wprost** (wiersz ``ResultsPublication`` z ``entry_totals``), a nie przez
``publish_results``: przedmiotem tych testów jest odczyt ogłoszonych sum, a nie przeliczenie etapu,
które wymaga zamkniętego okna reklamacji, skali i ocen. Kształt wiersza jest dokładnie taki, jaki
zapisuje ``publish_results`` (mapa ``{str(entry_id): suma}``, ``stage.results_published_at``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.models import GROUP_SUPERVISOR, CompetitionRole, SchoolSupervisor, Voivodeship
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.models import Problem, StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.results.models import ResultsPublication
from apps.schools.tests.factories import SchoolFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

SUPERVISOR_EMAIL = "opiekun@szkola.test"


@pytest.fixture
def flag_on(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "school_statistics": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition, year_label="2026/2027")


@pytest.fixture
def stage(edition):
    return StageFactory(edition=edition, kind=StageKind.ELIM, name="Eliminacje")


@pytest.fixture
def school():
    return SchoolFactory(name="XIV LO im. Staszica", city="Warszawa", voivodeship=Voivodeship.MAZOWIECKIE)


@pytest.fixture
def other_school():
    return SchoolFactory(name="V LO", city="Kraków", voivodeship=Voivodeship.MALOPOLSKIE)


def make_supervisor(competition, *, school=None, verified=True, email=SUPERVISOR_EMAIL):
    user = UserFactory(email=email, first_name="Anna", last_name="Opiekuńska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    return SchoolSupervisor.objects.create(
        user=user,
        competition=competition,
        school=school.name if school else "",
        school_ref=school,
        verified=verified,
    )


@pytest.fixture
def supervisor(competition, school):
    return make_supervisor(competition, school=school)


def student(
    stage, *, school=None, mine=False, status=StageEntryStatus.REGISTERED, submitted=True, late=False, **kw
):
    """Uczeń z wpisem do etapu. ``mine`` – wskazał opiekuna z fikstury ``supervisor``."""
    participant = ParticipantFactory(
        competition=stage.edition.competition,
        school=school.name if school else kw.pop("school_text", "LO bez wykazu"),
        school_ref=school,
        district=kw.pop("district", school.voivodeship if school else Voivodeship.MAZOWIECKIE),
        supervisor_email=kw.pop("supervisor_email", SUPERVISOR_EMAIL if mine else ""),
        **kw,
    )
    entry = StageEntryFactory(stage=stage, participant=participant, status=status)
    if submitted:
        problem = Problem.objects.filter(stage=stage).first() or ProblemFactory(stage=stage)
        SubmissionFactory(entry=entry, problem=problem, is_late=late)
    return entry


def publish(stage, totals: dict, *, qualified_only: bool = False):
    """Publikacja etapu w kształcie z ``publish_results``: mapa sum wpisów i znacznik na etapie."""
    now = timezone.now() - timedelta(minutes=1)
    publication, _created = ResultsPublication.objects.update_or_create(
        stage=stage,
        defaults={
            "published_at": now,
            "qualified_only": qualified_only,
            "snapshot": [],
            "entry_totals": {str(entry.pk): value for entry, value in totals.items()},
        },
    )
    stage.results_published_at = now
    stage.save(update_fields=["results_published_at"])
    return publication


@pytest.fixture
def supervisor_client(client_for, competition, supervisor):
    client = client_for(competition)
    client.force_login(supervisor.user)
    return client


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client
