"""Ekrany płatności: bramka flagi, role, izolacja, przelew z dowodem, eksport i ścieżka uczestnika."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.accounts.tests.test_delegations import leader_for_country
from apps.core.models import AuditLog
from apps.payments import services
from apps.payments.models import DocumentKind, OrderStatus, Payment, PaymentStatus, ScanStatus

from .conftest import BILLING, FakeResponse

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


# --- bramka flagi i role ---------------------------------------------------------------------------


def test_competition_without_fees_has_no_payment_screens(client_for, competition):
    from apps.accounts.tests.factories import CoordinatorFactory

    client = logged_in(client_for, competition, CoordinatorFactory())
    assert client.get("/coordinator/payments/").status_code == 404
    assert client.get("/coordinator/payments/prices/").status_code == 404
    html = client.get("/coordinator/").content.decode()
    assert "/coordinator/payments/" not in html


def test_menu_shows_payments_only_with_the_flag(client_for, iqo, coordinator):
    html = logged_in(client_for, iqo, coordinator).get("/coordinator/").content.decode()
    assert "/coordinator/payments/" in html


def test_participant_gets_403_on_coordinator_screens(client_for, iqo):
    participant = ParticipantFactory(competition=iqo)
    client = logged_in(client_for, iqo, participant.user)
    assert client.get("/coordinator/payments/").status_code == 403


def test_leader_sees_the_delegation_fees_and_the_dashboard_section(
    client_for, iqo, leader, students, price_list, billing
):
    client = logged_in(client_for, iqo, leader.user)
    page = client.get("/delegation/payments/")
    assert page.status_code == 200
    assert "250,00" in page.content.decode()  # formatowanie liczb według języka strony
    dashboard = client.get("/delegation/").content.decode()
    assert "/delegation/payments/" in dashboard


def test_leader_payment_screens_do_not_exist_without_the_flag(client_for, iqo, leader):
    iqo.feature_flags = {**iqo.feature_flags, "fees": False}
    iqo.save(update_fields=["feature_flags"])
    client = logged_in(client_for, iqo, leader.user)
    assert client.get("/delegation/payments/").status_code == 404
    assert "/delegation/payments/" not in client.get("/delegation/").content.decode()


def test_billing_form_then_prepare_creates_an_order_and_redirects_to_it(
    client_for, iqo, leader, students, price_list
):
    client = logged_in(client_for, iqo, leader.user)
    response = client.post("/delegation/payments/billing/", {**BILLING, "observers": 0})
    assert response.status_code == 302
    response = client.post("/delegation/payments/prepare/")
    order = leader.delegation.payment_orders.get()
    assert response["Location"].endswith(f"/payments/orders/{order.pk}/")
    assert order.total == Decimal("230.00")  # bez obserwatora
    page = client.get(f"/payments/orders/{order.pk}/").content.decode()
    assert order.reference in page


def test_order_page_is_404_for_a_leader_of_another_country(client_for, iqo, coordinator, order):
    other = leader_for_country(iqo, coordinator, "fr@example.test", "fr")
    client = logged_in(client_for, iqo, other.user)
    assert client.get(f"/payments/orders/{order.pk}/").status_code == 404
    proforma = order.documents.get()
    assert client.get(f"/payments/documents/{proforma.pk}/").status_code == 404
    assert client.post(f"/payments/orders/{order.pk}/cancel/").status_code == 404


def test_order_of_another_competition_is_404(client_for, other_competition, order, leader):
    other_competition.feature_flags = {"fees": True}
    other_competition.save(update_fields=["feature_flags"])
    client = logged_in(client_for, other_competition, leader.user)
    assert client.get(f"/payments/orders/{order.pk}/").status_code == 404


def test_pay_button_redirects_to_stripe_and_return_url_changes_nothing(
    client_for, iqo, leader, order, stripe_keys, fake_http
):
    fake_http["stripe"].return_value = FakeResponse(
        200, {"id": "cs_v", "url": "https://checkout.stripe.com/c/pay/cs_v"}
    )
    client = logged_in(client_for, iqo, leader.user)
    page = client.get(f"/payments/orders/{order.pk}/").content.decode()
    assert f"/payments/orders/{order.pk}/pay/stripe/" in page
    response = client.post(f"/payments/orders/{order.pk}/pay/stripe/")
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.stripe.com/")
    client.get(f"/payments/orders/{order.pk}/?checkout=success")
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN


def test_pay_requires_post(client_for, iqo, leader, order, stripe_keys):
    client = logged_in(client_for, iqo, leader.user)
    assert client.get(f"/payments/orders/{order.pk}/pay/stripe/").status_code == 405


def test_document_download_is_a_pdf_attachment(client_for, iqo, leader, order):
    proforma = order.documents.get()
    response = logged_in(client_for, iqo, leader.user).get(f"/payments/documents/{proforma.pk}/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]


# --- koordynator -------------------------------------------------------------------------------------


def test_dashboard_lists_delegations_totals_and_orders(client_for, iqo, coordinator, order):
    page = logged_in(client_for, iqo, coordinator).get("/coordinator/payments/")
    assert page.status_code == 200
    html = page.content.decode()
    assert order.reference in html and "EUR" in html


def test_prices_screen_saves_the_grid(client_for, iqo, coordinator, edition):
    client = logged_in(client_for, iqo, coordinator)
    response = client.post(
        "/coordinator/payments/prices/",
        {
            "edition": edition.pk,
            "currency": "eur",
            "STUDENT:REGULAR": "55",
            "DELEGATION:REGULAR": "120",
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    from apps.payments.pricing import price_list_for, prices_of

    price_list = price_list_for(edition)
    assert price_list.currency == "EUR"
    assert prices_of(price_list) == {
        ("STUDENT", "REGULAR"): Decimal("55.00"),
        ("DELEGATION", "REGULAR"): Decimal("120.00"),
    }


def test_bank_transfer_with_proof_marks_paid_and_scans_the_proof(
    client_for, iqo, coordinator, order, bank_account, django_capture_on_commit_callbacks, monkeypatch
):
    scanned = []
    monkeypatch.setattr("apps.payments.tasks.scan_payment_proof.delay", lambda pk: scanned.append(pk))
    client = logged_in(client_for, iqo, coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/coordinator/payments/orders/{order.pk}/mark-paid/",
            {
                "received_on": "2026-10-01",
                "note": "wyciąg 17",
                "proof": SimpleUploadedFile("p.pdf", PDF, "application/pdf"),
            },
        )
    assert response.status_code == 302
    order.refresh_from_db()
    payment = order.payments.get()
    assert order.status == OrderStatus.PAID
    assert payment.provider == "bank_transfer" and payment.status == PaymentStatus.SUCCEEDED
    assert payment.received_on == date(2026, 10, 1)
    assert payment.proof_scan_status == ScanStatus.PENDING and payment.proof_key.startswith("payments/")
    assert scanned == [payment.pk]
    assert order.documents.filter(kind=DocumentKind.INVOICE).exists()
    # Do pobrania dopiero po czystym skanie.
    assert client.get(f"/coordinator/payments/payments/{payment.pk}/proof/").status_code == 404
    services.apply_proof_verdict(payment.pk, ScanStatus.CLEAN)
    proof = client.get(f"/coordinator/payments/payments/{payment.pk}/proof/")
    assert proof.status_code == 200 and "attachment" in proof["Content-Disposition"]


def test_infected_proof_is_removed_but_the_payment_stays(order, coordinator, bank_account):
    payment = services.record_bank_transfer(
        order,
        received_on=date(2026, 10, 1),
        proof=SimpleUploadedFile("p.pdf", PDF, "application/pdf"),
        actor=coordinator,
    )
    services.apply_proof_verdict(payment.pk, ScanStatus.INFECTED)
    payment.refresh_from_db()
    assert payment.proof_scan_status == ScanStatus.INFECTED and payment.proof_key == ""
    assert payment.status == PaymentStatus.SUCCEEDED


def test_proof_that_is_not_a_document_is_refused(order, coordinator, bank_account):
    from apps.core.api import DomainError

    with pytest.raises(DomainError):
        services.record_bank_transfer(
            order,
            received_on=date(2026, 10, 1),
            proof=SimpleUploadedFile("x.pdf", b"<script>", "application/pdf"),
            actor=coordinator,
        )
    assert not Payment.objects.exists()


def test_bank_transfer_refund_is_recorded_manually(order, coordinator, bank_account):
    payment = services.record_bank_transfer(order, received_on=date(2026, 10, 1), actor=coordinator)
    refund = services.refund_payment(payment, amount="250", reason="Withdrawn", actor=coordinator)
    assert refund.status == "SUCCEEDED"
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED


def test_leader_cannot_mark_an_order_paid(client_for, iqo, leader, order):
    client = logged_in(client_for, iqo, leader.user)
    response = client.post(
        f"/coordinator/payments/orders/{order.pk}/mark-paid/", {"received_on": "2026-10-01"}
    )
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN


def test_csv_export_for_accounting(client_for, iqo, coordinator, order, edition):
    response = logged_in(client_for, iqo, coordinator).get(
        f"/coordinator/payments/export.csv?edition={edition.pk}"
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    assert order.reference in body and "DE123456789" in body
    assert AuditLog.objects.filter(action="payments.exported").exists()


def test_adjustment_screen_records_discount_with_reason(client_for, iqo, coordinator, delegation):
    client = logged_in(client_for, iqo, coordinator)
    response = client.post(
        f"/coordinator/payments/delegations/{delegation.pk}/adjustments/",
        {"kind": "DISCOUNT", "amount": "25", "reason": "Early bird partner"},
    )
    assert response.status_code == 302
    assert delegation.fee_adjustments.get().amount == Decimal("25.00")
    assert AuditLog.objects.filter(action="payments.adjustment_added").exists()


# --- uczestnik (konkurs z rejestracją otwartą) -------------------------------------------------------


@pytest.fixture
def participant_fee(competition):
    from apps.competitions.tests.factories import CurrentEditionFactory
    from apps.tenancy.fees import assign_fee, create_fee_schedule

    from .conftest import enable_fees

    enable_fees(competition)
    edition = CurrentEditionFactory(competition=competition)
    create_fee_schedule(competition, edition=edition, name="Wpisowe", amount="49.99")
    user = UserFactory(email="ola@example.test", first_name="Ola", last_name="Nowak", groups=["participant"])
    participant = ParticipantFactory(competition=competition, user=user)
    return assign_fee(participant, edition)


def test_participant_pays_online_and_the_fee_register_sees_it(
    client_for, competition, participant_fee, stripe_keys, fake_http
):
    from apps.tenancy.fees import FeeStatus

    client = logged_in(client_for, competition, participant_fee.participant.user)
    assert "/me/fees/pay/" in client.get("/me/").content.decode()
    response = client.post("/me/fees/pay/", {**BILLING, "buyer_type": "PERSON", "buyer_name": "Ola Nowak"})
    order = participant_fee.payment_orders.get()
    assert response["Location"].endswith(f"/payments/orders/{order.pk}/")
    assert order.total == Decimal("49.99") and order.currency == "PLN"
    payment = Payment.objects.create(order=order, provider="stripe", amount=order.total, currency="PLN")
    services.apply_success(payment, provider_payment_id="pi_x")
    participant_fee.refresh_from_db()
    assert participant_fee.status == FeeStatus.PAID
    assert participant_fee.external_reference == order.reference


def test_participant_full_refund_reaches_the_fee_register(competition, participant_fee):
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.tenancy.fees import FeeStatus

    order = services.prepare_participant_order(
        participant_fee, actor=participant_fee.participant.user, **{**BILLING, "buyer_type": "PERSON"}
    )
    services.save_settings(
        competition, actor=CoordinatorFactory(), bank_account="PL61109010140000071219812874"
    )
    coordinator = CoordinatorFactory()
    payment = services.record_bank_transfer(order, received_on=date(2026, 10, 1), actor=coordinator)
    services.refund_payment(payment, amount="49.99", reason="Withdrew", actor=coordinator)
    participant_fee.refresh_from_db()
    assert participant_fee.status == FeeStatus.REFUNDED


def test_participant_cannot_pay_someone_elses_fee(competition, participant_fee):
    from django.http import Http404

    stranger = ParticipantFactory(competition=competition)
    with pytest.raises(Http404):
        services.prepare_participant_order(participant_fee, actor=stranger.user, **BILLING)
