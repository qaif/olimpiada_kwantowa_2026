"""Polityka SEC-01: role platformy, tryb ``auto`` (funkcje wrażliwe), tryb ``custom``, uczestnik nigdy."""

from __future__ import annotations

import pytest

from apps.accounts import twofactor
from apps.accounts.models import GROUP_PARTICIPANT, GROUP_REVIEWER, GROUP_SUPERVISOR, GROUP_TEAM_LEADER
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.staff_mfa import policy
from apps.staff_mfa.models import PolicyMode, TwoFactorPolicy

from .conftest import enable_fees, super_coordinator

pytestmark = pytest.mark.django_db


def test_the_platform_default_covers_the_super_coordinator_and_admin(settings):
    """Wartość domyślna z ``config/settings/base.py`` – role widzące wszystkie konkursy naraz."""
    from config.settings import base

    settings.TWO_FACTOR_REQUIRED_ROLES = base.TWO_FACTOR_REQUIRED_ROLES
    assert policy.platform_roles() == {"superkoordynator", "admin"}


def test_super_coordinator_and_staff_are_required_by_platform_roles(settings, competition):
    settings.TWO_FACTOR_REQUIRED_ROLES = ["superkoordynator", "admin"]

    assert twofactor.is_required_for(super_coordinator(), competition)
    assert twofactor.is_required_for(UserFactory(is_staff=True), competition)
    assert not twofactor.is_required_for(CoordinatorFactory(), competition)


def test_a_competition_without_sensitive_features_requires_nothing_by_default(competition):
    assert policy.sensitive_features(competition) == []
    assert not twofactor.is_required_for(CoordinatorFactory(), competition)


def test_fees_make_coordinators_and_team_leaders_required(competition):
    enable_fees(competition)

    assert policy.sensitive_features(competition) == ["fees"]
    assert twofactor.is_required_for(CoordinatorFactory(), competition)
    assert twofactor.is_required_for(UserFactory(groups=[GROUP_TEAM_LEADER]), competition)
    # Komitet nie jest w domyślnym zestawie (prace bez danych szczególnych) – dokłada go ``custom``.
    assert not twofactor.is_required_for(UserFactory(groups=[GROUP_REVIEWER]), competition)


def test_delegation_mode_counts_as_a_sensitive_feature(competition):
    from apps.accounts.tests.test_delegations import make_delegations_competition

    make_delegations_competition(competition, edition=False)

    assert "delegations" in policy.sensitive_features(competition)
    assert twofactor.is_required_for(CoordinatorFactory(), competition)


def test_a_logistics_grant_is_a_staff_role(competition):
    from apps.delegation_logistics.models import AccessRole, LogisticsAccess

    enable_fees(competition)
    volunteer = UserFactory()
    LogisticsAccess.objects.create(competition=competition, user=volunteer, role=AccessRole.CHECKIN)

    assert policy.role_keys_of(volunteer, competition) == {"logistics"}
    assert twofactor.is_required_for(volunteer, competition)


def test_custom_mode_uses_exactly_the_chosen_roles(competition):
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, mode=PolicyMode.CUSTOM, roles=["reviewer"])

    assert twofactor.is_required_for(UserFactory(groups=[GROUP_REVIEWER]), competition)
    # Tryb ``custom`` zastępuje automat – koordynator bez zaznaczenia nie jest wymagany.
    assert not twofactor.is_required_for(CoordinatorFactory(), competition)


def test_custom_mode_with_no_roles_switches_the_competition_requirement_off(competition):
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, mode=PolicyMode.CUSTOM, roles=[])

    assert not twofactor.is_required_for(CoordinatorFactory(), competition)


def test_a_participant_can_never_be_required(settings, competition):
    """Ani przez ``.env``, ani przez politykę konkursu – SEC-01 § 0."""
    settings.TWO_FACTOR_REQUIRED_ROLES = [GROUP_PARTICIPANT]
    TwoFactorPolicy.objects.create(
        competition=competition, mode=PolicyMode.CUSTOM, roles=["participant", *policy.COMPETITION_ROLE_KEYS]
    )
    participant = ParticipantFactory(competition=competition)

    assert "participant" not in policy.required_roles(competition)
    assert not twofactor.is_required_for(participant.user, competition)


def test_the_supervisor_is_required_only_when_chosen(competition):
    supervisor = UserFactory(groups=[GROUP_SUPERVISOR])
    enable_fees(competition)
    assert not twofactor.is_required_for(supervisor, competition)

    TwoFactorPolicy.objects.create(competition=competition, mode=PolicyMode.CUSTOM, roles=["supervisor"])
    assert twofactor.is_required_for(supervisor, competition)


def test_the_requirement_is_per_competition(competition, other_competition):
    """Ten sam koordynator: wymagany tam, gdzie są płatności, a nie tam, gdzie ich nie ma."""
    enable_fees(competition)
    coordinator = CoordinatorFactory()

    assert twofactor.is_required_for(coordinator, competition)
    assert not twofactor.is_required_for(coordinator, other_competition)


def test_the_master_switch_wins_over_every_policy(settings, competition):
    enable_fees(competition)
    settings.TWO_FACTOR_ENABLED = False

    assert not twofactor.is_required_for(CoordinatorFactory(), competition)
    assert twofactor.requirement(CoordinatorFactory(), competition) is None


def test_the_grace_period_starts_once_and_is_not_renewed(competition):
    from datetime import timedelta

    from django.utils import timezone

    from apps.staff_mfa.models import TwoFactorGrace

    enable_fees(competition)
    coordinator = CoordinatorFactory()
    first = twofactor.requirement(coordinator, competition)
    TwoFactorGrace.objects.filter(user=coordinator).update(required_since=timezone.now() - timedelta(days=30))

    again = twofactor.requirement(coordinator, competition)

    assert TwoFactorGrace.objects.filter(user=coordinator).count() == 1
    assert not first.overdue()
    assert again.overdue()


def test_the_competition_may_shorten_the_grace_period(competition):
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, grace_days=0)

    assert twofactor.requirement(CoordinatorFactory(), competition).overdue()


def test_saving_the_policy_changes_its_version():
    before = policy.version()
    policy.bump_version()

    assert policy.version() != before
