"""Ekran „Webhooki płatności” ``/coordinator/fees/payments/``.

Przedmiotem są trzy rzeczy, których nie widać po kodzie widoku:

- ekran jest **wyłączony** bez flagi ``fees`` i daje wtedy 404, a nie 403 (§ 2.1),
- sekret podpisu pokazuje się **dokładnie raz** – po założeniu poświadczenia i po wymianie –
  i jedzie przez sesję, a nie przez ``messages``: komunikaty bywają renderowane ponownie na
  kolejnym ekranie, a to jest sekret,
- dziennik pokazuje wyłącznie doręczenia **niedopasowane**, z powodem rozwiniętym w zdanie:
  to jest jedyna rzecz, po którą organizator na ten ekran przychodzi.

Poświadczenie sąsiada nie daje się wymienić nawet z podstawionym identyfikatorem w adresie (§ 3.6).
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.integrations.inbound import create_payment_endpoint, record_event
from apps.integrations.models import UNMATCHED_UNKNOWN_REFERENCE
from apps.tenancy.fees import FEATURE
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

PAYMENTS_URL = "/coordinator/fees/payments/"


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client, user


@pytest.fixture
def coordinator_client(client_for, competition):
    enable(competition)
    client, _user = coordinator_for(client_for, competition)
    return client


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    client, _user = coordinator_for(client_for, competition)

    assert client.get(PAYMENTS_URL).status_code == 404
    assert client.post(PAYMENTS_URL, {"provider": "przelewy24"}).status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory().user)

    assert client.get(PAYMENTS_URL).status_code == 403

    enable(competition)

    assert client.get(PAYMENTS_URL).status_code == 403


def test_secret_is_shown_once_after_creation(coordinator_client, competition):
    from apps.integrations.models import PaymentEndpoint

    response = coordinator_client.post(PAYMENTS_URL, {"provider": "przelewy24"}, follow=True)
    endpoint = PaymentEndpoint.objects.get(competition=competition, provider="przelewy24")

    assert endpoint.secret in response.content.decode()
    # Odświeżenie strony sekretu już nie pokazuje – widok zdejmuje go z sesji przez ``pop``.
    assert endpoint.secret not in coordinator_client.get(PAYMENTS_URL).content.decode()


def test_rotation_replaces_the_secret_and_shows_the_new_one(coordinator_client, competition):
    endpoint = create_payment_endpoint(competition, provider="tpay")
    old = endpoint.secret

    response = coordinator_client.post(f"{PAYMENTS_URL}{endpoint.pk}/rotate/", follow=True)

    endpoint.refresh_from_db()
    assert endpoint.secret != old
    assert endpoint.secret in response.content.decode()
    assert old not in response.content.decode()


def test_no_secret_reaches_the_audit_log(coordinator_client, competition):
    coordinator_client.post(PAYMENTS_URL, {"provider": "payu"})
    from apps.integrations.models import PaymentEndpoint

    endpoint = PaymentEndpoint.objects.get(competition=competition, provider="payu")
    coordinator_client.post(f"{PAYMENTS_URL}{endpoint.pk}/rotate/")
    endpoint.refresh_from_db()

    dumped = "".join(str(row.diff) for row in AuditLog.objects.all())
    assert endpoint.secret not in dumped


def test_unmatched_deliveries_are_listed_with_a_reason(coordinator_client, competition):
    endpoint = create_payment_endpoint(competition, provider="przelewy24")
    record_event(
        endpoint,
        {"reference": "NIEZNANY-1", "event_id": "evt-1", "status": "paid", "amount": None},
        delivery_key="evt-1",
        body=b"{}",
        reason=UNMATCHED_UNKNOWN_REFERENCE,
    )

    content = coordinator_client.get(PAYMENTS_URL).content.decode()

    assert "NIEZNANY-1" in content
    assert "Żadna należność nie ma tego identyfikatora wpłaty." in content


def test_matched_deliveries_are_not_listed(coordinator_client, competition):
    """Dziennik odpowiada na pytanie „czego nie umieliśmy dopasować”, a nie „co przyszło”."""
    endpoint = create_payment_endpoint(competition, provider="przelewy24")
    record_event(
        endpoint,
        {"reference": "DOPASOWANE-1", "event_id": "evt-2", "status": "paid", "amount": None},
        delivery_key="evt-2",
        body=b"{}",
    )

    assert "DOPASOWANE-1" not in coordinator_client.get(PAYMENTS_URL).content.decode()


def test_foreign_endpoint_cannot_be_rotated(client_for, competition, other_competition):
    enable(other_competition)
    foreign = create_payment_endpoint(other_competition, provider="przelewy24")
    old = foreign.secret
    enable(competition)
    client, _user = coordinator_for(client_for, competition)

    response = client.post(f"{PAYMENTS_URL}{foreign.pk}/rotate/")

    foreign.refresh_from_db()
    assert response.status_code == 404
    assert foreign.secret == old
