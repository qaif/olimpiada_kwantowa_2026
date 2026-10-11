"""Stripe i Przelewy24 bez sieci: sesja z kwotą z serwera, podpisy webhooków, idempotencja, zwroty."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from django.core import mail

from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.payments import services
from apps.payments.models import (
    DocumentKind,
    OrderStatus,
    Payment,
    PaymentStatus,
    ProviderEvent,
    Refund,
    RefundStatus,
)
from apps.payments.providers import przelewy24 as p24
from apps.payments.providers import stripe

from .conftest import STRIPE_SECRET, FakeResponse, all_lines, stripe_event

pytestmark = pytest.mark.django_db

STRIPE_URL = "/payments/webhooks/stripe/"
P24_URL = "/payments/webhooks/przelewy24/"
P24_REFUND_URL = "/payments/webhooks/przelewy24/refund/"


def stripe_session(fake_http, session_id="cs_test_1"):
    fake_http["stripe"].return_value = FakeResponse(
        200, {"id": session_id, "url": f"https://checkout.stripe.com/c/pay/{session_id}"}
    )


def start_stripe(order, leader, fake_http, session_id="cs_test_1"):
    stripe_session(fake_http, session_id)
    url = services.start_checkout(order, "stripe", actor=leader.user)
    return url, Payment.objects.get(provider_ref=session_id)


def post_stripe(client, body: bytes, *, secret=STRIPE_SECRET, timestamp=None):
    return client.post(
        STRIPE_URL,
        data=body,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=stripe.sign(body, secret, timestamp),
    )


def completed(payment, *, amount=None, currency="eur", event_id="evt_paid"):
    return stripe_event(
        "checkout.session.completed",
        {
            "id": payment.provider_ref,
            "payment_status": "paid",
            "amount_total": amount if amount is not None else 25000,
            "currency": currency,
            "payment_intent": "pi_123",
            "metadata": {"payment_uuid": str(payment.uuid)},
        },
        event_id=event_id,
    )


# --- Stripe: sesja Checkout -------------------------------------------------------------------------


def test_checkout_amount_comes_from_the_order_not_from_the_client(order, leader, stripe_keys, fake_http):
    url, payment = start_stripe(order, leader, fake_http)
    assert urlsplit(url).hostname == "checkout.stripe.com"
    data = fake_http["stripe"].call_args.kwargs["data"]
    assert data["line_items[0][price_data][unit_amount]"] == 25000
    assert data["line_items[0][price_data][currency]"] == "eur"
    assert data["metadata[payment_uuid]"] == str(payment.uuid)
    assert fake_http["stripe"].call_args.kwargs["headers"]["Idempotency-Key"] == f"checkout-{payment.uuid}"
    assert payment.amount == Decimal("250.00") and payment.status == PaymentStatus.PENDING


def test_checkout_redirect_to_a_foreign_host_is_refused(order, leader, stripe_keys, fake_http):
    fake_http["stripe"].return_value = FakeResponse(200, {"id": "cs_x", "url": "https://evil.example/pay"})
    with pytest.raises(DomainError):
        services.start_checkout(order, "stripe", actor=leader.user)


def test_provider_error_marks_the_attempt_failed(order, leader, stripe_keys, fake_http):
    fake_http["stripe"].return_value = FakeResponse(402, {"error": {"type": "card_error", "code": "x"}})
    with pytest.raises(DomainError) as error:
        services.start_checkout(order, "stripe", actor=leader.user)
    assert error.value.machine_code == "PROVIDER_UNAVAILABLE"
    assert Payment.objects.get(order=order).status == PaymentStatus.FAILED


def test_stripe_is_unavailable_without_keys(order, leader):
    with pytest.raises(DomainError) as error:
        services.start_checkout(order, "stripe", actor=leader.user)
    assert error.value.machine_code == "METHOD_UNAVAILABLE"


def test_second_attempt_expires_the_previous_session(order, leader, stripe_keys, fake_http):
    _url, first = start_stripe(order, leader, fake_http, "cs_first")
    stripe_session(fake_http, "cs_second")
    services.start_checkout(order, "stripe", actor=leader.user)
    first.refresh_from_db()
    assert first.status == PaymentStatus.CANCELLED
    paths = [call.args[1] for call in fake_http["stripe"].call_args_list]
    assert f"{stripe.API_BASE}/checkout/sessions/cs_first/expire" in paths


def test_someone_else_cannot_start_a_checkout(order, iqo, coordinator, stripe_keys, fake_http):
    from django.http import Http404

    from apps.accounts.tests.test_delegations import leader_for_country

    other = leader_for_country(iqo, coordinator, "fr@example.test", "fr")
    with pytest.raises(Http404):
        services.start_checkout(order, "stripe", actor=other.user)


# --- Stripe: podpis ---------------------------------------------------------------------------------


def test_signature_accepts_any_of_several_secrets_and_rejects_stale_ones():
    body = b'{"id": "evt"}'
    header = stripe.sign(body, "whsec_b")
    assert stripe.verify_signature(body, header, ["whsec_a", "whsec_b"])
    assert not stripe.verify_signature(body, header, ["whsec_a"])
    assert not stripe.verify_signature(body + b" ", header, ["whsec_b"])
    old = stripe.sign(body, "whsec_b", int(time.time()) - stripe.TOLERANCE_SECONDS - 5)
    assert not stripe.verify_signature(body, old, ["whsec_b"])
    assert not stripe.verify_signature(body, "garbage", ["whsec_b"])


# --- Stripe: webhook ---------------------------------------------------------------------------------


def test_paid_webhook_marks_the_order_paid_issues_invoice_and_mails_the_payer(
    order, leader, stripe_keys, fake_http, client_for, iqo, django_capture_on_commit_callbacks
):
    _url, payment = start_stripe(order, leader, fake_http)
    with django_capture_on_commit_callbacks(execute=True):
        response = post_stripe(client_for(iqo), completed(payment))
    assert response.status_code == 200
    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == OrderStatus.PAID and order.paid_at is not None
    assert payment.status == PaymentStatus.SUCCEEDED and payment.provider_payment_id == "pi_123"
    invoice = order.documents.get(kind=DocumentKind.INVOICE)
    assert invoice.snapshot["payment_method"] == "stripe"
    recipients = {address for message in mail.outbox for address in message.to}
    assert {"billing@example.test", "lead@example.test"} <= recipients
    assert any(order.reference in message.subject for message in mail.outbox)


def test_receipt_and_documents_are_in_the_payers_language(
    iqo,
    leader,
    delegation,
    students,
    price_list,
    billing,
    english_enabled_site,
    stripe_keys,
    fake_http,
    client_for,
    django_capture_on_commit_callbacks,
):
    from apps.accounts.models import UserPreference

    english_enabled_site(iqo)
    UserPreference.objects.create(user=leader.user, language="en")
    delegation = type(delegation).objects.get(pk=delegation.pk)  # konkurs z bazy, jak w żądaniu
    order = services.prepare_delegation_order(delegation, actor=leader.user)
    assert order.language == "en"
    assert order.lines.get(kind="STUDENT").description == "Student"
    _url, payment = start_stripe(order, leader, fake_http)
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        post_stripe(client_for(iqo), completed(payment))
    assert mail.outbox and all(message.subject.startswith("Payment confirmation") for message in mail.outbox)
    assert "Thank you" in mail.outbox[0].body
    assert order.documents.get(kind=DocumentKind.INVOICE).language == "en"


def test_repeated_delivery_changes_nothing(order, leader, stripe_keys, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    body = completed(payment)
    assert post_stripe(client_for(iqo), body).status_code == 200
    second = post_stripe(client_for(iqo), body)
    assert second.status_code == 200 and second.json()["outcome"] == "duplicate"
    assert ProviderEvent.objects.filter(event_id="evt_paid").count() == 1
    assert order.documents.filter(kind=DocumentKind.INVOICE).count() == 1
    assert AuditLog.objects.filter(action="payments.order_paid").count() == 1


def test_bad_signature_is_rejected_and_leaves_nothing(order, leader, stripe_keys, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    response = post_stripe(client_for(iqo), completed(payment), secret="whsec_wrong")
    assert response.status_code == 400
    assert not ProviderEvent.objects.exists()
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN


def test_missing_signature_header_is_rejected(stripe_keys, client_for, iqo):
    response = client_for(iqo).post(STRIPE_URL, data=b"{}", content_type="application/json")
    assert response.status_code == 400


def test_webhook_without_configured_secret_does_not_exist(client_for, iqo):
    body = stripe_event("checkout.session.completed", {"id": "cs"})
    assert post_stripe(client_for(iqo), body).status_code == 404


def test_amount_mismatch_flags_the_payment_and_keeps_the_order_open(
    order, leader, stripe_keys, fake_http, client_for, iqo
):
    _url, payment = start_stripe(order, leader, fake_http)
    assert post_stripe(client_for(iqo), completed(payment, amount=100)).status_code == 200
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == PaymentStatus.MISMATCH
    assert order.status == OrderStatus.OPEN
    assert not order.documents.filter(kind=DocumentKind.INVOICE).exists()
    assert services.attention_payments(iqo)[0] == [payment]


def test_currency_mismatch_is_also_a_mismatch(order, leader, stripe_keys, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment, currency="usd"))
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.MISMATCH


def test_expired_session_cancels_the_attempt(order, leader, stripe_keys, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    body = stripe_event(
        "checkout.session.expired", {"id": payment.provider_ref, "metadata": {}}, event_id="evt_exp"
    )
    post_stripe(client_for(iqo), body)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.CANCELLED


def test_unknown_session_is_acknowledged(stripe_keys, client_for, iqo):
    body = stripe_event("checkout.session.completed", {"id": "cs_unknown", "payment_status": "paid"})
    response = post_stripe(client_for(iqo), body)
    assert response.status_code == 200
    assert ProviderEvent.objects.get().outcome == "unknown_payment"


def test_second_payment_of_a_paid_order_is_a_duplicate_for_refund(
    order, leader, stripe_keys, fake_http, client_for, iqo
):
    _url, first = start_stripe(order, leader, fake_http, "cs_a")
    second = Payment.objects.create(
        order=order, provider="stripe", amount=order.total, currency="EUR", provider_ref="cs_b"
    )
    post_stripe(client_for(iqo), completed(first, event_id="evt_a"))
    post_stripe(client_for(iqo), completed(second, event_id="evt_b"))
    second.refresh_from_db()
    assert second.status == PaymentStatus.MISMATCH
    assert "podwójna" in second.detail


# --- Stripe: zwrot ------------------------------------------------------------------------------------


def paid_by_stripe(order, leader, fake_http, client_for, iqo):
    _url, payment = start_stripe(order, leader, fake_http)
    post_stripe(client_for(iqo), completed(payment))
    payment.refresh_from_db()
    return payment


def test_full_refund_through_the_api_refunds_the_order(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    payment = paid_by_stripe(order, leader, fake_http, client_for, iqo)
    fake_http["stripe"].return_value = FakeResponse(200, {"id": "re_1", "status": "succeeded"})
    refund = services.refund_payment(
        payment, lines=all_lines(order), reason="Delegation withdrew", actor=coordinator
    )
    assert refund.status == RefundStatus.SUCCEEDED and refund.provider_refund_id == "re_1"
    data = fake_http["stripe"].call_args.kwargs["data"]
    assert data == {
        "payment_intent": "pi_123",
        "amount": 25000,
        "metadata[refund_uuid]": refund.uuid.hex,
        "metadata[payment_uuid]": str(payment.uuid),
    }
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED and order.refunded_amount == Decimal("250.00")
    assert AuditLog.objects.filter(action="payments.refunded").exists()


def test_partial_refund_keeps_the_order_paid_and_caps_the_amount(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    payment = paid_by_stripe(order, leader, fake_http, client_for, iqo)
    fake_http["stripe"].return_value = FakeResponse(200, {"id": "re_2", "status": "pending"})
    student = order.lines.get(kind="STUDENT")
    refund = services.refund_payment(
        payment, lines={student.pk: 1}, reason="One student withdrew", actor=coordinator
    )
    assert refund.status == RefundStatus.PENDING
    with pytest.raises(DomainError) as error:
        services.refund_payment(payment, lines={student.pk: 2}, reason="too much", actor=coordinator)
    assert error.value.machine_code == "REFUND_QUANTITY"
    body = stripe_event("refund.updated", {"id": "re_2", "status": "succeeded"}, event_id="evt_ref")
    post_stripe(client_for(iqo), body)
    order.refresh_from_db()
    assert order.status == OrderStatus.PAID and order.refunded_amount == Decimal("50.00")


def test_refund_is_coordinator_only_and_needs_a_reason(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    payment = paid_by_stripe(order, leader, fake_http, client_for, iqo)
    with pytest.raises(DomainError):
        services.refund_payment(payment, lines=all_lines(order), reason="x", actor=leader.user)
    with pytest.raises(DomainError):
        services.refund_payment(payment, lines=all_lines(order), reason=" ", actor=coordinator)
    assert not Refund.objects.exists()


def test_refused_refund_is_recorded_as_failed(
    order, leader, coordinator, stripe_keys, fake_http, client_for, iqo
):
    payment = paid_by_stripe(order, leader, fake_http, client_for, iqo)
    fake_http["stripe"].return_value = FakeResponse(400, {"error": {"type": "invalid_request_error"}})
    with pytest.raises(DomainError):
        services.refund_payment(payment, lines=all_lines(order), reason="test", actor=coordinator)
    assert Refund.objects.get().status == RefundStatus.FAILED


# --- Przelewy24 ---------------------------------------------------------------------------------------


@pytest.fixture
def pln_order(order, iqo, edition, coordinator):
    """To samo zamówienie w PLN – P24 obsługuje wyłącznie złotówki."""
    type(order).objects.filter(pk=order.pk).update(currency="PLN")
    order.refresh_from_db()
    return order


def p24_notification(payment, *, amount=25000, crc="p24-crc", order_id=777):
    data = {
        "merchantId": 11111,
        "posId": 11111,
        "sessionId": str(payment.uuid),
        "amount": amount,
        "originAmount": amount,
        "currency": "PLN",
        "orderId": order_id,
        "methodId": 25,
        "statement": "p24-X",
    }
    data["sign"] = p24.notification_sign(data, crc)
    return json.dumps(data).encode()


def test_p24_registers_the_transaction_with_a_signed_server_amount(pln_order, leader, p24_keys, fake_http):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "TOKEN-1"}, "responseCode": 0})
    url = services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    assert url == "https://sandbox.przelewy24.pl/trnRequest/TOKEN-1"
    payload = fake_http["p24"].call_args.kwargs["json"]
    assert payload["amount"] == 25000 and payload["currency"] == "PLN"
    expected = p24._sign(
        {
            "sessionId": payload["sessionId"],
            "merchantId": 11111,
            "amount": 25000,
            "currency": "PLN",
            "crc": "p24-crc",
        }
    )
    assert payload["sign"] == expected
    assert payload["urlStatus"].endswith(P24_URL)


def test_p24_is_not_offered_for_euro(order, leader, p24_keys):
    assert "przelewy24" not in services.available_methods(order)


def test_p24_notification_is_verified_before_the_payment_is_recorded(
    pln_order, leader, p24_keys, fake_http, client_for, iqo
):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    payment = Payment.objects.get(order=pln_order)
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"status": "success"}})
    response = client_for(iqo).post(P24_URL, data=p24_notification(payment), content_type="application/json")
    assert response.status_code == 200
    verify = fake_http["p24"].call_args
    assert verify.args[0] == "PUT" and verify.args[1].endswith("/transaction/verify")
    assert verify.kwargs["json"]["orderId"] == 777
    pln_order.refresh_from_db()
    assert pln_order.status == OrderStatus.PAID


def test_p24_failed_verification_asks_for_a_retry(pln_order, leader, p24_keys, fake_http, client_for, iqo):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    payment = Payment.objects.get(order=pln_order)
    fake_http["p24"].return_value = FakeResponse(400, {"error": "x"})
    response = client_for(iqo).post(P24_URL, data=p24_notification(payment), content_type="application/json")
    assert response.status_code == 503
    assert not ProviderEvent.objects.exists()  # wycofane – ponowienie od dostawcy przejdzie od nowa
    pln_order.refresh_from_db()
    assert pln_order.status == OrderStatus.OPEN


def test_p24_bad_signature_is_rejected(pln_order, leader, p24_keys, fake_http, client_for, iqo):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    payment = Payment.objects.get(order=pln_order)
    response = client_for(iqo).post(
        P24_URL, data=p24_notification(payment, crc="wrong"), content_type="application/json"
    )
    assert response.status_code == 400


def test_p24_attempt_in_progress_blocks_a_new_one(pln_order, leader, p24_keys, fake_http):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    with pytest.raises(DomainError) as error:
        services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    assert error.value.machine_code == "PAYMENT_IN_PROGRESS"


def test_p24_refund_completes_on_its_notification(
    pln_order, leader, coordinator, p24_keys, fake_http, client_for, iqo
):
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"token": "T"}})
    services.start_checkout(pln_order, "przelewy24", actor=leader.user)
    payment = Payment.objects.get(order=pln_order)
    fake_http["p24"].return_value = FakeResponse(200, {"data": {"status": "success"}})
    client_for(iqo).post(P24_URL, data=p24_notification(payment), content_type="application/json")
    payment.refresh_from_db()
    fake_http["p24"].return_value = FakeResponse(201, {"data": [{"status": True}], "responseCode": 0})
    refund = services.refund_payment(
        payment, lines=all_lines(pln_order), reason="Cancelled", actor=coordinator
    )
    assert refund.status == RefundStatus.PENDING
    note = {
        "orderId": 777,
        "sessionId": str(payment.uuid),
        "merchantId": 11111,
        "requestId": refund.uuid.hex,
        "refundsUuid": refund.uuid.hex,
        "amount": 25000,
        "currency": "PLN",
        "timestamp": 1,
        "status": 0,
    }
    note["sign"] = p24.refund_notification_sign(note, "p24-crc")
    response = client_for(iqo).post(
        P24_REFUND_URL, data=json.dumps(note).encode(), content_type="application/json"
    )
    assert response.status_code == 200
    refund.refresh_from_db()
    pln_order.refresh_from_db()
    assert refund.status == RefundStatus.SUCCEEDED
    assert pln_order.status == OrderStatus.REFUNDED
