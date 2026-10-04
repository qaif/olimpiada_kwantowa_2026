"""Świat testów płatności: Konkurs #1 przestawiony na delegacje (jak ``iqo``) z flagą ``fees``.

Żaden test nie wychodzi do sieci: Stripe i Przelewy24 są podmieniane na poziomie ``requests``
(:func:`fake_http`), a webhooki podpisujemy tymi samymi funkcjami, którymi liczy je dostawca.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest import mock

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.competitions.services import current_edition
from apps.payments import services

STRIPE_SECRET = "whsec_test_secret"


def enable_fees(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def iqo(competition):
    make_delegations_competition(competition)
    return enable_fees(competition)


@pytest.fixture
def edition(iqo):
    return current_edition(iqo)


@pytest.fixture
def coordinator(iqo):
    return CoordinatorFactory()


@pytest.fixture
def leader(iqo, coordinator):
    return leader_for_country(iqo, coordinator, "lead@example.test", "de")


@pytest.fixture
def delegation(leader):
    return leader.delegation


@pytest.fixture
def students(leader):
    return [add(leader, email=f"kid{index}@example.test") for index in range(2)]


PRICES = {
    "DELEGATION:REGULAR": Decimal("100"),
    "STUDENT:REGULAR": Decimal("50"),
    "STUDENT:LATE": Decimal("80"),
    "LEADER:REGULAR": Decimal("30"),
    "OBSERVER:REGULAR": Decimal("20"),
}


@pytest.fixture
def price_list(iqo, edition, coordinator):
    return services.save_price_list(
        iqo, edition, currency="EUR", early_until=None, late_from=None, prices=dict(PRICES), actor=coordinator
    )


BILLING = {
    "buyer_type": "INSTITUTION",
    "buyer_name": "Bundesministerium",
    "buyer_address": "Unter den Linden 1, Berlin",
    "buyer_country": "Germany",
    "buyer_vat_id": "DE123456789",
    "buyer_email": "billing@example.test",
}


@pytest.fixture
def billing(delegation, leader):
    return services.save_billing_profile(delegation=delegation, actor=leader.user, observers=1, **BILLING)


@pytest.fixture
def order(delegation, leader, students, price_list, billing):
    """Zamówienie: delegacja 100 + 2 uczniów × 50 + 1 opiekun × 30 + 1 obserwator × 20 = 250 EUR."""
    return services.prepare_delegation_order(delegation, actor=leader.user)


@pytest.fixture
def stripe_keys(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_123"
    settings.STRIPE_WEBHOOK_SECRET = STRIPE_SECRET
    return settings


@pytest.fixture
def p24_keys(settings):
    settings.P24_MERCHANT_ID = 11111
    settings.P24_POS_ID = 11111
    settings.P24_API_KEY = "p24-api-key"
    settings.P24_CRC = "p24-crc"
    settings.P24_SANDBOX = True
    return settings


@pytest.fixture
def bank_account(iqo, coordinator):
    return services.save_settings(
        iqo, actor=coordinator, bank_account="PL61109010140000071219812874", bank_swift="WBKPPLPP"
    )


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture
def fake_http():
    """``requests.post``/``requests.request`` podmienione – test ustawia odpowiedzi i czyta wywołania."""
    with (
        mock.patch("apps.payments.providers.stripe.send") as stripe_post,
        mock.patch("apps.payments.providers.przelewy24.send") as p24_request,
    ):
        yield {"stripe": stripe_post, "p24": p24_request}


def stripe_event(event_type: str, obj: dict, event_id: str = "evt_1", livemode: bool = False) -> bytes:
    return json.dumps(
        {"id": event_id, "type": event_type, "livemode": livemode, "data": {"object": obj}}
    ).encode()


def all_lines(order) -> dict[int, int]:
    """Zwrot całego zamówienia: każda pozycja (bez zniżki) w pełnej ilości."""
    return {line.pk: line.quantity for line in order.lines.exclude(kind="DISCOUNT")}
