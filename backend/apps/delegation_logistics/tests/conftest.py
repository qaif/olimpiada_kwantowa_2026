"""Świat testów logistyki finału: Konkurs #1 przestawiony na delegacje (jak ``iqo``) z flagą logistyki.

Delegacja Niemiec ma opiekuna i dwóch uczniów (jeden niepełnoletni), delegacja Francji – opiekuna.
Oficer logistyki to koordynator z przydziałem ``OFFICER``; drugi koordynator przydziału nie ma.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.delegation_logistics.models import FLAG, AccessRole, FinalEvent, LogisticsAccess


def enable(competition, *, health: bool = False):
    competition.feature_flags = {**(competition.feature_flags or {}), FLAG: True}
    competition.save(update_fields=["feature_flags"])
    if health:
        from apps.competitions.logistics import LogisticsSettings

        LogisticsSettings.objects.update_or_create(
            competition=competition, defaults={"collect_special_needs": True}
        )
    return competition


def years_ago(years: int, extra_days: int = 0) -> date:
    today = timezone.localdate()
    return today.replace(year=today.year - years) - timedelta(days=extra_days)


@pytest.fixture
def iqo(competition):
    return enable(make_delegations_competition(competition))


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def officer(iqo, coordinator):
    LogisticsAccess.objects.create(competition=iqo, user=coordinator, role=AccessRole.OFFICER)
    return coordinator


@pytest.fixture
def leader(iqo, coordinator):
    return leader_for_country(iqo, coordinator, "lead-de@example.test", country="de")


@pytest.fixture
def other_leader(iqo, coordinator):
    return leader_for_country(iqo, coordinator, "lead-fr@example.test", country="fr")


@pytest.fixture
def students(leader):
    adult = add(
        leader, email="adult@example.test", first_name="Anna", last_name="Adult", birth_date=years_ago(19)
    )
    minor = add(
        leader, email="minor@example.test", first_name="Max", last_name="Minor", birth_date=years_ago(16)
    )
    return adult, minor


@pytest.fixture
def event(iqo):
    from apps.competitions.services import current_edition

    start = timezone.localdate() + timedelta(days=60)
    return FinalEvent.objects.create(
        competition=iqo,
        edition=current_edition(iqo),
        name="IQO 2027",
        city="Warsaw",
        starts_on=start,
        ends_on=start + timedelta(days=6),
        letter_prefix="IQO",
    )


@pytest.fixture
def checkin_user(iqo, coordinator):
    user = UserFactory(email="volunteer@example.test")
    LogisticsAccess.objects.create(
        competition=iqo, user=user, role=AccessRole.CHECKIN, granted_by=coordinator
    )
    return user
