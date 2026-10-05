"""Token API a polityka SEC-01 (§ 6): w okresie przejściowym jak dotąd, po nim – bez tokenu."""

from __future__ import annotations

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.tests.factories import CoordinatorFactory
from apps.staff_mfa.models import TwoFactorPolicy

from .conftest import PASSWORD, enable_fees, enable_for, fresh_code

pytestmark = pytest.mark.django_db

ME_URL = "/api/auth/me/"


def _client(competition):
    host = competition.primary_domain or competition.site.hostname
    return APIClient(HTTP_HOST=host, SERVER_NAME=host)


def _login(competition, user, **extra):
    return _client(competition).post(
        "/api/auth/login/", {"email": user.email, "password": PASSWORD, **extra}, format="json"
    )


def _with_token(competition, key):
    client = _client(competition)
    client.credentials(HTTP_AUTHORIZATION=f"Token {key}")
    return client


@pytest.fixture(autouse=True)
def _hosts(client_for, competition):
    """``client_for`` dopisuje domeny testowe do ``ALLOWED_HOSTS`` – tu wyłącznie dla tego efektu."""
    client_for(competition)


def test_in_the_grace_period_a_required_coordinator_still_gets_a_token(competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()

    response = _login(competition, coordinator)

    assert response.status_code == 200
    assert _with_token(competition, response.json()["token"]).get(ME_URL).status_code == 200


def test_after_the_grace_period_no_token_and_the_old_token_stops(competition):
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, grace_days=0)
    coordinator = CoordinatorFactory()
    stale = Token.objects.create(user=coordinator)

    response = _login(competition, coordinator)

    assert response.status_code == 403
    assert response.json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"
    assert _with_token(competition, stale.key).get(ME_URL).status_code == 401


def test_a_coordinator_with_a_device_gets_a_token_only_with_the_code(competition):
    enable_fees(competition)
    coordinator = CoordinatorFactory()
    enable_for(coordinator)

    without = _login(competition, coordinator)
    with_code = _login(competition, coordinator, code=fresh_code(coordinator))

    assert without.status_code == 400 and without.json()["code"] == "TWO_FACTOR_REQUIRED"
    assert with_code.status_code == 200
    assert _with_token(competition, with_code.json()["token"]).get(ME_URL).status_code == 200


def test_the_token_of_a_free_competition_is_untouched(competition, other_competition):
    """Polityka liczy się w konkursie żądania: tam, gdzie wymogu nie ma, token działa jak dotąd."""
    enable_fees(competition)
    TwoFactorPolicy.objects.create(competition=competition, grace_days=0)
    coordinator = CoordinatorFactory()

    response = _login(other_competition, coordinator)

    assert response.status_code == 200
