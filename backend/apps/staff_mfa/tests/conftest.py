"""Świat testów SEC-01: funkcja włączona, polityka platformy pusta, chyba że test mówi inaczej.

Pusta polityka platformy w fiksturze to nie wybieg: test ma deklarować **wprost**, od kogo 2FA jest
wymagane, a nie dziedziczyć wartości domyślnej ``superkoordynator,admin`` (tę sprawdza osobny test).
"""

from __future__ import annotations

import pytest

from apps.accounts import twofactor
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory

PASSWORD = DEFAULT_PASSWORD


@pytest.fixture(autouse=True)
def _feature_on(settings):
    settings.TWO_FACTOR_ENABLED = True
    settings.TWO_FACTOR_REQUIRED_ROLES = []
    settings.TWO_FACTOR_GRACE_DAYS = 14
    settings.TWO_FACTOR_REMEMBER_DAYS = 7


def enable_fees(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def enable_for(user) -> list[str]:
    device = twofactor.begin_setup(user)
    return twofactor.confirm_setup(
        user, twofactor.totp_code(device.plain_secret(), twofactor.current_counter())
    )


def fresh_code(user) -> str:
    """Kod z **następnego** kroku – bieżący jest już spalony przez ``confirm_setup``."""
    device = twofactor.device_for(user)
    return twofactor.totp_code(device.plain_secret(), twofactor.current_counter() + 1)


def super_coordinator():
    from apps.accounts.super_coordinator import grant

    user = UserFactory(email="super@example.test")
    grant(user)
    return user


@pytest.fixture
def client(client_for, competition):
    return client_for(competition)
