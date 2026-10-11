"""Fikstury testów webinarów: fałszywy LiveKit, magazyn w pamięci, konkurs z flagą i konta w rolach."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
)
from apps.competitions.tests.factories import CurrentEditionFactory, StageEntryFactory, StageFactory
from apps.tenancy.tests.factories import grant_membership
from apps.webinars import livekit
from apps.webinars.models import Webinar

from .fake_livekit import API_KEY, API_SECRET, STORAGE_PATH, URL, FakeLiveKit, MemoryRecordingStorage


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, settings):
    """Siatka: bez ``fake_livekit`` każde wywołanie sieci jest błędem testu; magazyn – w pamięci."""

    def refuse(url, body, headers, timeout):  # pragma: no cover - ma się nigdy nie wykonać
        raise AssertionError("Test próbował połączyć się z prawdziwym serwerem LiveKit.")

    monkeypatch.setattr(livekit, "_http_post", refuse)
    settings.WEBINARS_STORAGE_BACKEND = STORAGE_PATH
    MemoryRecordingStorage.reset()


@pytest.fixture
def fake_livekit(settings, monkeypatch):
    """Skonfigurowany serwer LiveKit w pamięci. Żaden test tego pakietu nie wychodzi do sieci."""
    settings.LIVEKIT_URL = URL
    settings.LIVEKIT_API_URL = ""
    settings.LIVEKIT_API_KEY = API_KEY
    settings.LIVEKIT_API_SECRET = API_SECRET
    settings.LIVEKIT_TOKEN_TTL_SECONDS = 600
    settings.LIVEKIT_TIMEOUT_SECONDS = 8
    settings.WEBINAR_JOIN_LEAD_MINUTES = 15
    settings.WEBINAR_JOIN_GRACE_MINUTES = 30
    settings.WEBINAR_REMINDER_MINUTES = 60
    fake = FakeLiveKit()
    monkeypatch.setattr(livekit, "_http_post", fake)
    return fake


@pytest.fixture
def webinars_on(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "webinars": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition)


@pytest.fixture
def stage(edition):
    return StageFactory(edition=edition, competition=edition.competition)


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory(first_name="Ola", last_name="Koordynatorka")
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def participant(competition, stage):
    profile = ParticipantFactory(competition=competition)
    profile.user.first_name, profile.user.last_name = "Ala", "Nowak"
    profile.user.save(update_fields=["first_name", "last_name"])
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(participant=profile, stage=stage, competition=competition)
    return profile


@pytest.fixture
def reviewer(competition):
    member = ActiveReviewerFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    return member


def make_webinar(competition, *, starts_in_minutes: int = 5, **kwargs) -> Webinar:
    values = {
        "title": "Omówienie zadań",
        "starts_at": timezone.now() + timedelta(minutes=starts_in_minutes),
        "duration_minutes": 60,
    }
    values.update(kwargs)
    return Webinar.objects.create(competition=competition, **values)


@pytest.fixture
def logged(client_for, competition):
    def make(user):
        client = client_for(competition)
        client.force_login(user)
        return client

    return make
