"""Fikstury testów reguły zaświadczeń z warsztatów."""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.tenancy.tests.factories import grant_membership

from .helpers import current_stage, workshops_page_for


@pytest.fixture
def stage(competition):
    """Konkurs #1 z harmonogramem czterech warsztatów i edycją bieżącą z jednym etapem."""
    workshops_page_for(competition)
    return current_stage(competition)


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    client.user = user
    return client
