"""Cennik delegacji, pokrycie składu, zniżki, zwolnienia, zamówienia i numeracja dokumentów (PAY-01)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.http import Http404

from apps.accounts.tests.test_delegations import add, leader_for_country
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.payments import documents, pricing, services
from apps.payments.models import (
    BillingDocument,
    DocumentKind,
    Order,
    OrderStatus,
    PriceKind,
    PriceList,
    PricePeriod,
)

from .conftest import PRICES

pytestmark = pytest.mark.django_db


# --- okresy cenowe ----------------------------------------------------------------------------------


def test_period_boundaries_are_inclusive_for_early_and_late():
    price_list = PriceList(early_until=date(2026, 3, 1), late_from=date(2026, 5, 1))
    assert price_list.period_on(date(2026, 3, 1)) == PricePeriod.EARLY
    assert price_list.period_on(date(2026, 3, 2)) == PricePeriod.REGULAR
    assert price_list.period_on(date(2026, 4, 30)) == PricePeriod.REGULAR
    assert price_list.period_on(date(2026, 5, 1)) == PricePeriod.LATE


def test_missing_period_price_falls_back_to_regular():
    prices = {(PriceKind.STUDENT, PricePeriod.REGULAR): Decimal("50")}
    assert pricing.unit_price(prices, PriceKind.STUDENT, PricePeriod.EARLY) == Decimal("50")
    assert pricing.unit_price(prices, PriceKind.OBSERVER, PricePeriod.EARLY) is None


def test_price_list_refuses_late_before_early(iqo, edition, coordinator):
    with pytest.raises(Exception):  # noqa: B017 - ValidationError z full_clean albo więz bazy
        services.save_price_list(
            iqo,
            edition,
            currency="EUR",
            early_until=date(2026, 5, 1),
            late_from=date(2026, 4, 1),
            prices={},
            actor=coordinator,
        )


def test_price_list_is_coordinator_only(iqo, edition, leader):
    with pytest.raises(DomainError) as error:
        services.save_price_list(iqo, edition, currency="EUR", prices={}, actor=leader.user)
    assert error.value.machine_code == "COORDINATOR_REQUIRED"


# --- zestawienie i zamówienie ---------------------------------------------------------------------


def test_statement_counts_delegation_students_leaders_and_observers(
    delegation, students, price_list, billing
):
    statement = pricing.statement(delegation)
    assert statement.state == pricing.STATE_DUE
    assert statement.composition == {"DELEGATION": 1, "STUDENT": 2, "LEADER": 1, "OBSERVER": 1}
    assert statement.new_total == Decimal("250.00")
    assert statement.currency == "EUR"


def test_order_snapshots_lines_buyer_and_issues_a_numbered_proforma(order, iqo):
    assert order.status == OrderStatus.OPEN
    assert order.total == Decimal("250.00")
    assert order.currency == "EUR"
    assert order.buyer_vat_id == "DE123456789"
    assert [(line.kind, line.quantity, line.amount) for line in order.lines.all()] == [
        ("DELEGATION", 1, Decimal("100.00")),
        ("STUDENT", 2, Decimal("100.00")),
        ("LEADER", 1, Decimal("30.00")),
        ("OBSERVER", 1, Decimal("20.00")),
    ]
    proforma = order.documents.get(kind=DocumentKind.PROFORMA)
    assert proforma.number == f"{iqo.slug.upper()}/PF/{proforma.year}/0001"
    assert proforma.snapshot["buyer"]["name"] == "Bundesministerium"
    assert AuditLog.objects.filter(action="payments.order_created", target_id=str(order.pk)).exists()


def test_covered_items_are_not_charged_twice_and_only_the_increment_is_ordered(order, delegation, leader):
    with pytest.raises(DomainError) as error:
        services.prepare_delegation_order(delegation, actor=leader.user)
    assert error.value.machine_code == "NOTHING_TO_PAY"
    add(leader, email="third@example.test")
    second = services.prepare_delegation_order(delegation, actor=leader.user)
    assert [(line.kind, line.quantity) for line in second.lines.all()] == [("STUDENT", 1)]
    assert second.total == Decimal("50.00")


def test_late_student_pays_the_late_price_and_earlier_ones_keep_theirs(
    order, delegation, leader, coordinator, edition, iqo
):
    services.save_price_list(
        iqo,
        edition,
        currency="EUR",
        late_from=pricing.competition_today(iqo) - timedelta(days=1),
        prices=dict(PRICES),
        actor=coordinator,
    )
    add(leader, email="late@example.test")
    late = services.prepare_delegation_order(delegation, actor=leader.user)
    line = late.lines.get()
    assert (line.kind, line.unit_price) == ("STUDENT", Decimal("80.00"))
    assert late.period == PricePeriod.LATE
    assert order.lines.get(kind="STUDENT").unit_price == Decimal("50.00")


def test_cancelled_order_stops_covering(order, delegation, leader):
    services.cancel_order(order, actor=leader.user)
    assert pricing.statement(delegation).new_total == Decimal("250.00")


def test_discount_reduces_the_next_order_once(delegation, students, price_list, billing, leader, coordinator):
    services.add_adjustment(
        delegation, kind="DISCOUNT", amount="70", reason="Solidarity fund", actor=coordinator
    )
    order = services.prepare_delegation_order(delegation, actor=leader.user)
    assert order.total == Decimal("180.00")
    assert order.lines.get(kind="DISCOUNT").amount == Decimal("-70.00")
    add(leader, email="next@example.test")
    assert services.prepare_delegation_order(delegation, actor=leader.user).total == Decimal("50.00")


def test_waiver_blocks_new_orders_and_requires_a_reason(
    delegation, students, price_list, billing, leader, coordinator
):
    with pytest.raises(DomainError):
        services.add_adjustment(delegation, kind="WAIVER", reason="  ", actor=coordinator)
    adjustment = services.add_adjustment(delegation, kind="WAIVER", reason="Host country", actor=coordinator)
    assert pricing.statement(delegation).state == pricing.STATE_WAIVED
    with pytest.raises(DomainError) as error:
        services.prepare_delegation_order(delegation, actor=leader.user)
    assert error.value.machine_code == "WAIVED"
    services.revoke_adjustment(adjustment, reason="Mistake", actor=coordinator)
    assert pricing.statement(delegation).state == pricing.STATE_DUE
    assert AuditLog.objects.filter(action="payments.adjustment_revoked").exists()


def test_adjustments_are_coordinator_only(delegation, leader):
    with pytest.raises(DomainError):
        services.add_adjustment(delegation, kind="WAIVER", reason="please", actor=leader.user)


def test_order_requires_billing_details(delegation, students, price_list, leader):
    with pytest.raises(DomainError) as error:
        services.prepare_delegation_order(delegation, actor=leader.user)
    assert error.value.machine_code == "BILLING_PROFILE_MISSING"


def test_leader_of_another_country_cannot_order_for_this_delegation(
    iqo, coordinator, delegation, students, price_list, billing
):
    other = leader_for_country(iqo, coordinator, "fr-lead@example.test", "fr")
    with pytest.raises(DomainError):
        services.prepare_delegation_order(delegation, actor=other.user)
    with pytest.raises(Http404):
        services.save_billing_profile(delegation=delegation, actor=other.user, **{"buyer_name": "x"})


# --- numeracja ------------------------------------------------------------------------------------


def test_numbering_is_sequential_per_competition_kind_and_year(order, delegation, leader, other_competition):
    add(leader, email="seq@example.test")
    second = services.prepare_delegation_order(delegation, actor=leader.user)
    numbers = sorted(
        BillingDocument.objects.filter(kind=DocumentKind.PROFORMA).values_list("sequence", flat=True)
    )
    assert numbers == [1, 2]
    services.apply_success(
        services.Payment.objects.create(
            order=second, provider="bank_transfer", amount=second.total, currency="EUR"
        )
    )
    invoice = second.documents.get(kind=DocumentKind.INVOICE)
    assert invoice.sequence == 1  # osobna seria faktur
    assert "/FV/" in invoice.number


def test_document_pdf_renders_from_the_snapshot(order):
    proforma = order.documents.get(kind=DocumentKind.PROFORMA)
    pdf = documents.render_pdf(proforma)
    assert pdf.startswith(b"%PDF")
    assert documents.filename(proforma).endswith(".pdf")
    assert "/" not in documents.filename(proforma)


def test_document_language_falls_back_to_english_for_scripts_without_glyphs():
    assert documents.document_language("zh-hans") == "en"
    assert documents.document_language("ru") == "ru"


def test_orders_of_another_competition_are_invisible(order, other_competition):
    assert not Order.objects.for_competition(other_competition).exists()
