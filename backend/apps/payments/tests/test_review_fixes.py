"""Poprawki po przeglądzie PAY-01 (H1, M1–M5, L2–L7): dostęp, wyścigi prób, zwroty pozycjami, sprzątanie."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import requests
from django.utils import timezone

from apps.accounts import delegation_services
from apps.accounts.tests.test_delegations import add, leader_for_country
from apps.core.api import DomainError
from apps.payments import pricing, services
from apps.payments.models import (
    BillingProfile,
    OrderStatus,
    Payment,
    PaymentStatus,
    ProviderEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers import stripe
from apps.payments.providers.stripe import SESSION_TTL_SECONDS

from .conftest import BILLING, FakeResponse, all_lines, stripe_event
from .test_providers import completed, post_stripe, start_stripe

pytestmark = pytest.mark.django_db

CHECKOUT_URL = f"{stripe.API_BASE}/checkout/sessions"


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def age(payment, **delta):
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(**delta))


# --- H1 / L2: opiekun odwołany ------------------------------------------------------------------------


def test_removed_leader_cannot_view_pay_cancel_or_download(
    client_for, iqo, coordinator, leader, order, stripe_keys, fake_http
):
    proforma = order.documents.get()
    delegation_services.remove_leader(leader, actor=coordinator)
    client = logged_in(client_for, iqo, leader.user)
    assert client.get("/delegation/payments/").status_code in (403, 404)
    assert client.get(f"/payments/orders/{order.pk}/").status_code == 404
    assert client.post(f"/payments/orders/{order.pk}/pay/stripe/").status_code == 404
    assert client.post(f"/payments/orders/{order.pk}/cancel/").status_code == 404
    assert client.get(f"/payments/documents/{proforma.pk}/").status_code == 404
    assert not fake_http["stripe"].called
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN


def test_composition_counts_only_active_leaders(iqo, coordinator, leader, delegation):
    second = leader_for_country(iqo, coordinator, "co-lead@example.test", "de")
    assert pricing.composition(delegation)["LEADER"] == 2
    delegation_services.remove_leader(second, actor=coordinator)
    assert pricing.composition(delegation)["LEADER"] == 1


# --- M1: wyścig dwóch prób ---------------------------------------------------------------------------


def test_session_of_a_superseded_attempt_is_expired_at_once(order, leader, stripe_keys, fake_http):
    calls = []

    def respond(method, url, **kwargs):
        calls.append((method, url))
        if url == CHECKOUT_URL:
            # Równoległe żądanie zamknęło tę próbę, zanim Stripe oddał sesję.
            Payment.objects.filter(order=order).update(status=PaymentStatus.CANCELLED)
            return FakeResponse(200, {"id": "cs_new", "url": "https://checkout.stripe.com/c/pay/cs_new"})
        return FakeResponse(200, {})

    fake_http["stripe"].side_effect = respond
    with pytest.raises(DomainError) as error:
        services.start_checkout(order, "stripe", actor=leader.user)
    assert error.value.machine_code == "PAYMENT_IN_PROGRESS"
    assert ("POST", f"{CHECKOUT_URL}/cs_new/expire") in calls
    assert not Payment.objects.filter(provider_ref="cs_new").exists()


def test_attempt_without_session_id_blocks_while_young_and_is_closed_when_old(
    order, leader, stripe_keys, fake_http
):
    stale = Payment.objects.create(order=order, provider="stripe", amount=order.total, currency="EUR")
    with pytest.raises(DomainError) as error:
        services.start_checkout(order, "stripe", actor=leader.user)
    assert error.value.machine_code == "PAYMENT_IN_PROGRESS"
    assert not fake_http["stripe"].called
    age(stale, seconds=services.STRIPE_UNREFERENCED_GRACE_SECONDS + 5)
    fake_http["stripe"].return_value = FakeResponse(
        200, {"id": "cs_ok", "url": "https://checkout.stripe.com/c/pay/cs_ok"}
    )
    services.start_checkout(order, "stripe", actor=leader.user)
    stale.refresh_from_db()
    assert stale.status == PaymentStatus.CANCELLED


# --- M2: nieudane wygaszenie i sprzątanie --------------------------------------------------------------


@pytest.mark.parametrize(
    ("session_state", "allowed"), [("expired", True), ("open", False), ("complete", False)]
)
def test_failed_expire_checks_the_session_before_deciding(
    order, leader, stripe_keys, fake_http, session_state, allowed
):
    _url, first = start_stripe(order, leader, fake_http, "cs_first")

    def respond(method, url, **kwargs):
        if url.endswith("/expire"):
            return FakeResponse(400, {"error": {"type": "invalid_request_error"}})
        if method == "GET":
            return FakeResponse(200, {"status": session_state, "payment_status": "unpaid"})
        return FakeResponse(200, {"id": "cs_second", "url": "https://checkout.stripe.com/c/pay/cs_second"})

    fake_http["stripe"].side_effect = respond
    if allowed:
        services.start_checkout(order, "stripe", actor=leader.user)
        first.refresh_from_db()
        assert first.status == PaymentStatus.CANCELLED
    else:
        with pytest.raises(DomainError) as error:
            services.start_checkout(order, "stripe", actor=leader.user)
        assert error.value.machine_code == "PAYMENT_IN_PROGRESS"


def test_sweeper_settles_a_paid_session_whose_webhook_was_lost(order, leader, stripe_keys, fake_http):
    _url, payment = start_stripe(order, leader, fake_http, "cs_lost")
    age(payment, seconds=SESSION_TTL_SECONDS + 700)
    fake_http["stripe"].return_value = FakeResponse(
        200,
        {
            "status": "complete",
            "payment_status": "paid",
            "amount_total": 25000,
            "currency": "eur",
            "payment_intent": "pi_lost",
        },
    )
    services.sweep_payments()
    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == OrderStatus.PAID and payment.provider_payment_id == "pi_lost"


def test_sweeper_closes_expired_and_abandoned_attempts(order, leader, stripe_keys, p24_keys, fake_http):
    _url, expired = start_stripe(order, leader, fake_http, "cs_old")
    age(expired, seconds=SESSION_TTL_SECONDS + 700)
    blank = Payment.objects.create(order=order, provider="stripe", amount=order.total, currency="EUR")
    age(blank, minutes=5)
    p24 = Payment.objects.create(
        order=order, provider="przelewy24", amount=order.total, currency="EUR", provider_ref="x"
    )
    age(p24, minutes=services.P24_ABANDON_MINUTES + 61)
    fake_http["stripe"].return_value = FakeResponse(200, {"status": "expired"})
    services.sweep_payments()
    for payment in (expired, blank, p24):
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.CANCELLED


# --- M3: zwroty pozycjami ------------------------------------------------------------------------------


@pytest.fixture
def paid_order(order, coordinator, bank_account):
    services.record_bank_transfer(order, received_on=date(2026, 10, 1), actor=coordinator)
    order.refresh_from_db()
    return order


def test_replacement_student_pays_only_after_the_withdrawn_one_is_refunded(
    paid_order, delegation, leader, students, coordinator
):
    delegation_services.remove_student(leader, students[0])
    add(leader, email="replacement@example.test")
    with pytest.raises(DomainError) as error:
        services.prepare_delegation_order(delegation, actor=leader.user)
    assert error.value.machine_code == "NOTHING_TO_PAY"  # miejsce nadal opłacone
    payment = paid_order.payments.get()
    student_line = paid_order.lines.get(kind="STUDENT")
    refund = services.refund_payment(
        payment, lines={student_line.pk: 1}, reason="Withdrawn", actor=coordinator
    )
    assert refund.amount == Decimal("50.00") and refund.lines.get().quantity == 1
    assert pricing.covered(delegation)["STUDENT"] == 1
    replacement_order = services.prepare_delegation_order(delegation, actor=leader.user)
    assert [(line.kind, line.quantity) for line in replacement_order.lines.all()] == [("STUDENT", 1)]


def test_refund_of_an_order_payment_requires_lines(paid_order, coordinator):
    payment = paid_order.payments.get()
    with pytest.raises(DomainError) as error:
        services.refund_payment(payment, lines={}, reason="x", actor=coordinator)
    assert error.value.machine_code == "REFUND_LINES_REQUIRED"
    discount_free = {line.pk: line.quantity + 1 for line in paid_order.lines.all()}
    with pytest.raises(DomainError) as error:
        services.refund_payment(payment, lines=discount_free, reason="x", actor=coordinator)
    assert error.value.machine_code == "REFUND_QUANTITY"


def test_full_refund_frees_every_item_for_a_new_order(paid_order, delegation, leader, coordinator):
    services.refund_payment(
        paid_order.payments.get(), lines=all_lines(paid_order), reason="x", actor=coordinator
    )
    paid_order.refresh_from_db()
    assert paid_order.status == OrderStatus.REFUNDED
    assert pricing.statement(delegation).new_total == Decimal("250.00")


def test_mismatch_payment_is_refunded_whole_and_without_lines(
    order, leader, stripe_keys, fake_http, client_for, iqo, coordinator
):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment, amount=100))
    payment.refresh_from_db()
    with pytest.raises(DomainError) as error:
        services.refund_payment(payment, lines=all_lines(order), reason="x", actor=coordinator)
    assert error.value.machine_code == "REFUND_LINES_NOT_ALLOWED"
    fake_http["stripe"].return_value = FakeResponse(200, {"id": "re_m", "status": "succeeded"})
    refund = services.refund_payment(payment, reason="wrong amount", actor=coordinator)
    assert refund.amount == Decimal("250.00") and refund.status == RefundStatus.SUCCEEDED
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN  # zamówienie dalej czeka na właściwą wpłatę


def test_coordinator_refund_screen_posts_lines(client_for, iqo, coordinator, paid_order):
    payment = paid_order.payments.get()
    line = paid_order.lines.get(kind="OBSERVER")
    client = logged_in(client_for, iqo, coordinator)
    page = client.get(f"/coordinator/payments/orders/{paid_order.pk}/").content.decode()
    assert f'name="line_{line.pk}"' in page
    response = client.post(
        f"/coordinator/payments/payments/{payment.pk}/refund/",
        {"reason": "Observer stayed home", f"line_{line.pk}": "1"},
    )
    assert response.status_code == 302
    assert Refund.objects.get().amount == Decimal("20.00")


# --- M4: podwójne „Zapisz wpłatę” -----------------------------------------------------------------------


def test_double_bank_transfer_post_records_one_payment(client_for, iqo, coordinator, order, bank_account):
    client = logged_in(client_for, iqo, coordinator)
    for _ in range(2):
        client.post(f"/coordinator/payments/orders/{order.pk}/mark-paid/", {"received_on": "2026-10-01"})
    assert order.payments.count() == 1
    with pytest.raises(DomainError) as error:
        services.record_bank_transfer(order, received_on=date(2026, 10, 2), actor=coordinator)
    assert error.value.machine_code == "ORDER_NOT_OPEN"


# --- M5: zwrot o nieznanym wyniku --------------------------------------------------------------------------


def test_refund_timeout_stays_pending_and_is_retried_with_the_same_key(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment))
    payment.refresh_from_db()
    fake_http["stripe"].side_effect = requests.Timeout()
    refund = services.refund_payment(payment, lines=all_lines(order), reason="Withdrew", actor=coordinator)
    assert refund.status == RefundStatus.PENDING and refund.provider_refund_id == ""
    first_key = fake_http["stripe"].call_args.kwargs["headers"]["Idempotency-Key"]
    assert first_key == f"refund-{refund.uuid.hex}"
    Refund.objects.filter(pk=refund.pk).update(created_at=timezone.now() - timedelta(minutes=10))
    fake_http["stripe"].side_effect = None
    fake_http["stripe"].return_value = FakeResponse(200, {"id": "re_retry", "status": "succeeded"})
    services.sweep_payments()
    refund.refresh_from_db()
    assert refund.status == RefundStatus.SUCCEEDED and refund.provider_refund_id == "re_retry"
    assert fake_http["stripe"].call_args.kwargs["headers"]["Idempotency-Key"] == first_key


def test_refund_webhook_falls_back_to_our_refund_uuid(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment))
    payment.refresh_from_db()
    fake_http["stripe"].side_effect = requests.ConnectionError()
    refund = services.refund_payment(payment, lines=all_lines(order), reason="Withdrew", actor=coordinator)
    body = stripe_event(
        "refund.updated",
        {"id": "re_late", "status": "succeeded", "metadata": {"refund_uuid": refund.uuid.hex}},
        event_id="evt_refund_late",
    )
    assert post_stripe(client_for(iqo), body).status_code == 200
    refund.refresh_from_db()
    assert refund.status == RefundStatus.SUCCEEDED and refund.provider_refund_id == "re_late"


def test_refused_refund_is_failed_not_retried(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment))
    payment.refresh_from_db()
    fake_http["stripe"].return_value = FakeResponse(400, {"error": {"type": "invalid_request_error"}})
    with pytest.raises(DomainError):
        services.refund_payment(payment, lines=all_lines(order), reason="x", actor=coordinator)
    assert Refund.objects.get().status == RefundStatus.FAILED


# --- L3, L4, L5, L6, L7 ------------------------------------------------------------------------------------


def test_p24_refund_ids_fit_32_characters(pln_order_paid_by_p24, coordinator, fake_http):
    payment = pln_order_paid_by_p24
    fake_http["p24"].return_value = FakeResponse(201, {"data": [{"status": True}]})
    services.refund_payment(payment, lines=all_lines(payment.order), reason="x", actor=coordinator)
    payload = fake_http["p24"].call_args.kwargs["json"]
    assert len(payload["refundsUuid"]) == 32 and "-" not in payload["requestId"]


@pytest.fixture
def pln_order_paid_by_p24(order, leader, p24_keys, fake_http, client_for, iqo):
    from apps.payments.providers import przelewy24 as p24

    from .test_providers import p24_notification

    type(order).objects.filter(pk=order.pk).update(currency="PLN")
    order.refresh_from_db()
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(order, "przelewy24", actor=leader.user)
    payment = Payment.objects.get(order=order)
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"status": "success"}})
    client_for(iqo).post(
        "/payments/webhooks/przelewy24/", data=p24_notification(payment), content_type="application/json"
    )
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED and p24
    return payment


def test_live_event_is_ignored_with_a_test_key(order, leader, stripe_keys, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    body = stripe_event(
        "checkout.session.completed",
        {"id": payment.provider_ref, "payment_status": "paid", "amount_total": 25000, "currency": "eur"},
        event_id="evt_live",
        livemode=True,
    )
    assert post_stripe(client_for(iqo), body).status_code == 200
    assert ProviderEvent.objects.get(event_id="evt_live").outcome == "mode_mismatch"
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN


def test_payment_on_a_cancelled_order_is_flagged_and_transfers_are_refused(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo, bank_account
):
    _url, payment = start_stripe(order, leader, fake_http)
    Payment.objects.filter(pk=payment.pk).update(status=PaymentStatus.CANCELLED)
    type(order).objects.filter(pk=order.pk).update(status=OrderStatus.CANCELLED)
    post_stripe(client_for(iqo), completed(payment))
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == PaymentStatus.MISMATCH and "anulowane" in payment.detail
    assert order.status == OrderStatus.CANCELLED
    with pytest.raises(DomainError):
        services.record_bank_transfer(order, received_on=date(2026, 10, 1), actor=coordinator)


def test_anonymised_account_loses_its_billing_profile_but_orders_stay(competition):
    from apps.accounts.profile import anonymise_account
    from apps.accounts.tests.factories import ParticipantFactory, UserFactory
    from apps.competitions.tests.factories import CurrentEditionFactory
    from apps.tenancy.fees import assign_fee, create_fee_schedule

    from .conftest import enable_fees

    enable_fees(competition)
    edition = CurrentEditionFactory(competition=competition)
    create_fee_schedule(competition, edition=edition, name="Wpisowe", amount="49.99")
    user = UserFactory(email="ola@example.test", groups=["participant"])
    fee = assign_fee(ParticipantFactory(competition=competition, user=user), edition)
    order = services.prepare_participant_order(fee, actor=user, **{**BILLING, "buyer_type": "PERSON"})
    assert BillingProfile.objects.filter(participant__user=user).exists()
    anonymise_account(user)
    assert not BillingProfile.objects.filter(participant__user=user).exists()
    order.refresh_from_db()
    assert order.buyer_name == BILLING["buyer_name"]  # dokument księgowy zostaje


def test_export_includes_delegation_billing_profiles_the_user_edited(leader, billing):
    rows = services.export_section(leader.user)
    assert any(row["rodzaj"].startswith("dane nabywcy delegacji") for row in rows)


def test_webhooks_have_their_own_throttle_scope(settings):
    from apps.payments.views.webhooks import StripeWebhookView

    assert StripeWebhookView.throttle_scope == "payment_webhooks"
    from config.settings import base

    assert base.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["payment_webhooks"] == "600/min"
