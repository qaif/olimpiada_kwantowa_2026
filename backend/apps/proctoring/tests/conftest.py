"""Fikstury nadzoru: fałszywy LiveKit, magazyn w pamięci, etap z nadzorem, konta w rolach, delegacje.

Delegacje (DEL-01) są podstawiane przez :func:`delegations_map` – adapter ``apps.proctoring.delegations``
czyta wtedy słownik zamiast modeli, których na tej gałęzi jeszcze nie ma. Izolacja opiekuna jest
więc sprawdzana na **regule nadzoru** (pokój i zapytanie), niezależnie od tego, skąd przychodzi kraj.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.models import CommitteeStatus, CompetitionRole
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.proctoring import delegations
from apps.proctoring.models import ProctoringConfig
from apps.tenancy.tests.factories import grant_membership
from apps.webinars import livekit

from .fake_livekit import API_KEY, API_SECRET, STORAGE_PATH, URL, FakeLiveKit, MemoryStorage


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, settings):
    def refuse(url, body, headers, timeout):  # pragma: no cover - ma się nigdy nie wykonać
        raise AssertionError("Test próbował połączyć się z prawdziwym serwerem LiveKit.")

    monkeypatch.setattr(livekit, "_http_post", refuse)
    settings.PROCTORING_STORAGE_BACKEND = STORAGE_PATH
    settings.PROCTORING_WINDOW_ADAPTER = ""
    MemoryStorage.reset()


@pytest.fixture
def fake_livekit(settings, monkeypatch):
    settings.LIVEKIT_URL = URL
    settings.LIVEKIT_API_URL = ""
    settings.LIVEKIT_API_KEY = API_KEY
    settings.LIVEKIT_API_SECRET = API_SECRET
    settings.LIVEKIT_TOKEN_TTL_SECONDS = 600
    settings.LIVEKIT_TIMEOUT_SECONDS = 8
    fake = FakeLiveKit()
    monkeypatch.setattr(livekit, "_http_post", fake)
    return fake


@pytest.fixture
def proctoring_on(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "proctoring": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition)


@pytest.fixture
def stage(edition):
    return StageFactory(edition=edition, competition=edition.competition)


@pytest.fixture
def config(stage):
    return ProctoringConfig.objects.create(stage=stage, enabled=True)


def make_student(competition, stage, *, birth_year: int = 1990, first: str = "Ala", last: str = "Nowak"):
    profile = ParticipantFactory(competition=competition, birth_year=birth_year)
    profile.user.first_name, profile.user.last_name = first, last
    profile.user.save(update_fields=["first_name", "last_name"])
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(participant=profile, stage=stage, competition=competition)
    return profile


@pytest.fixture
def student(competition, stage):
    return make_student(competition, stage)


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory(first_name="Ola", last_name="Koordynatorka")
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def reviewer(competition):
    member = ActiveReviewerFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    return member.user


@pytest.fixture
def appeals_member(competition):
    from apps.accounts.models import GROUP_APPEALS

    member = CommitteeMemberFactory(
        competition=competition,
        user__groups=[GROUP_APPEALS],
        status=CommitteeStatus.ACTIVE,
        is_appeals_committee=True,
        district_verified=True,
    )
    grant_membership(member.user, competition, CompetitionRole.APPEALS)
    return member.user


@pytest.fixture
def leader():
    return UserFactory(first_name="Tom", last_name="Leader")


@pytest.fixture
def delegations_map(monkeypatch):
    """``state.students[pk] = id delegacji``, ``state.leaders[pk] = id`` – podstawia adapter DEL-01."""
    state = SimpleNamespace(students={}, leaders={})
    monkeypatch.setattr(
        delegations, "participant_delegation_id", lambda participant: state.students.get(participant.pk)
    )
    monkeypatch.setattr(
        delegations, "leader_delegation_id", lambda user, competition, stage=None: state.leaders.get(user.pk)
    )
    monkeypatch.setattr(delegations, "delegation_label", lambda delegation_id: f"kraj {delegation_id}")
    return state


@pytest.fixture
def logged(client_for, competition):
    def make(user):
        client = client_for(competition)
        client.force_login(user)
        return client

    return make


def ready_session(stage, participant, *, started=True):
    """Sesja ze zgodą, sprawdzonym sprzętem i (opcjonalnie) potwierdzonym startem."""
    from apps.proctoring import services

    session = services.session_for(stage, participant)
    services.give_consent(session, user=participant.user)
    session.check_passed_at = timezone.now()
    if started:
        session.started_at = timezone.now()
    session.save()
    return session
