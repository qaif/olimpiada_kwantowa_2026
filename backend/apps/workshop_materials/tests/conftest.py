"""Fikstury testów materiałów: magazyn w pamięci zamiast MinIO i konta w rolach konkursu."""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.tenancy.tests.factories import grant_membership

from .helpers import STORAGE_PATH, FakeMaterialStorage


@pytest.fixture(autouse=True)
def storage(settings):
    """Każdy test tego pakietu dostaje pusty magazyn w pamięci – do MinIO nie idzie nic."""
    settings.WORKSHOP_MATERIALS_STORAGE_BACKEND = STORAGE_PATH
    FakeMaterialStorage.reset()
    yield FakeMaterialStorage
    FakeMaterialStorage.reset()


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def participant_client(client_for, competition):
    profile = ParticipantFactory(competition=competition)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    client = client_for(competition)
    client.force_login(profile.user)
    client.user = profile.user
    return client
