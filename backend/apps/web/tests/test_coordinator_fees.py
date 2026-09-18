"""Ekrany wpisowego: cennik ``/coordinator/fees/`` i rejestr ``/coordinator/fees/register/``.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- oba adresy są **wyłączone** w konkursie z domyślnymi przełącznikami i dają wtedy 404, a nie 403:
  Olimpiada Kwantowa jest bezpłatna i po wdrożeniu wydania K ma mieć adresy dokładnie takie, jak
  przed nim (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu flagi – odpowiedź nie zdradza konfiguracji,
- zmiana cennika **nie rusza** należności już naliczonych (kwota jest kopią z chwili naliczenia),
- identyfikatora wpłaty nie da się podmienić po ustawieniu: to jest jedyna rzecz, po której webhook
  dopasowuje potwierdzenie, a dwa identyfikatory znaczą dwie płatności u dostawcy,
- rachunek **musi** pochodzić z szablonu (decyzja D15: rejestr, nie księgowość) i zapamiętuje jego
  wersję, więc pobrany za rok wyjdzie taki sam,
- cudzej należności nie widać ani na liście, ani pod adresem czynności (§ 3.6).

Adresy są w ``apps/web/urls.py``: montaż wydania K rozwinął tam ``urls_fees.urlpatterns``, więc
testy chodzą po mapie produkcyjnej. Ścieżki się nie zmieniły.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.tenancy.documents import DOCUMENTS_FLAG, DocumentKind, set_current_template
from apps.tenancy.fees import (
    FEATURE,
    FeeStatus,
    ParticipantFee,
    assign_fee,
    create_fee_schedule,
    schedules_for,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

FEES_URL = "/coordinator/fees/"
REGISTER_URL = "/coordinator/fees/register/"

AMOUNT = Decimal("49.99")


def enable(competition, *flags: str):
    """Włącza przełączniki tak, jak zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
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
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** wpisowym."""
    enable(competition, FEATURE)
    client, _user = coordinator_for(client_for, competition)
    return client


@pytest.fixture
def schedule(competition, edition):
    """Cennik podstawowy edycji bieżącej – 49,99 PLN z czternastodniowym terminem."""
    enable(competition, FEATURE)
    return create_fee_schedule(competition, edition=edition, name="Wpisowe 2026", amount=AMOUNT)


@pytest.fixture
def fee(schedule, participant, edition):
    """Należność jednego uczestnika, naliczona z cennika podstawowego."""
    return assign_fee(participant, edition)


def payload(**overrides) -> dict:
    """Komplet pól formularza cennika plus to, co test podmienia."""
    data = {"name": "Wpisowe", "amount": "50", "currency": "PLN", "due_days": "14"}
    data.update(overrides)
    return data


# --- przełącznik ekranu ---------------------------------------------------------------------------


def test_screens_are_off_for_a_competition_with_default_flags(client_for, competition):
    """Konkurs #1 po wdrożeniu: adresów nie ma, dopóki nikt świadomie nie włączy wpisowego."""
    client, _user = coordinator_for(client_for, competition)

    assert not competition.has_feature(FEATURE)
    assert client.get(FEES_URL).status_code == 404
    assert client.get(REGISTER_URL).status_code == 404
    assert client.post(FEES_URL, payload()).status_code == 404
    assert schedules_for(competition).count() == 0


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    """Rola sprawdza się przed flagą: odpowiedź nie mówi uczestnikowi, jak skonfigurowano konkurs."""
    user = ParticipantFactory().user
    client = client_for(competition)
    client.force_login(user)

    assert client.get(FEES_URL).status_code == 403

    enable(competition, FEATURE)

    assert client.get(FEES_URL).status_code == 403


def test_anonymous_is_redirected_to_login(client_for, competition):
    enable(competition, FEATURE)
    response = client_for(competition).get(REGISTER_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- cennik ---------------------------------------------------------------------------------------


def test_schedule_is_created_from_the_screen(coordinator_client, competition, edition):
    response = coordinator_client.post(FEES_URL, payload(edition=edition.pk, amount="49.99"))

    assert response.status_code == 302
    row = schedules_for(competition).get()
    assert row.amount == AMOUNT
    assert row.currency == "PLN"
    # Bramka z decyzji D16 jest domyślnie wyłączona i formularz jej sam nie włącza.
    assert row.blocks_submission is False


def test_second_base_schedule_of_one_edition_is_refused(coordinator_client, competition, edition):
    coordinator_client.post(FEES_URL, payload(edition=edition.pk))
    response = coordinator_client.post(FEES_URL, payload(edition=edition.pk, name="Drugie"))

    assert response.status_code == 400
    assert schedules_for(competition).count() == 1


def test_price_change_does_not_touch_issued_dues(coordinator_client, schedule, fee):
    """Kwota należności jest kopią z chwili naliczenia – podwyżka obowiązuje od następnej."""
    response = coordinator_client.post(
        f"{FEES_URL}{schedule.pk}/",
        {"name": schedule.name, "amount": "99.00", "currency": "PLN", "due_days": "14", "is_active": "on"},
    )

    assert response.status_code == 302
    schedule.refresh_from_db()
    fee.refresh_from_db()
    assert schedule.amount == Decimal("99.00")
    assert fee.amount == AMOUNT


def test_schedule_of_a_foreign_competition_is_404(client_for, competition, other_competition):
    """Cennik sąsiada nie daje się zmienić nawet z podstawionym identyfikatorem w adresie."""
    enable(other_competition, FEATURE)
    from apps.competitions.tests.factories import EditionFactory

    foreign_edition = EditionFactory(competition=other_competition)
    foreign = create_fee_schedule(
        other_competition, edition=foreign_edition, name="Cudze", amount=Decimal("10.00")
    )
    enable(competition, FEATURE)
    client, _user = coordinator_for(client_for, competition)

    response = client.post(f"{FEES_URL}{foreign.pk}/", payload())

    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.name == "Cudze"


# --- rejestr należności ----------------------------------------------------------------------------


def test_register_shows_the_row_and_the_totals(coordinator_client, fee, participant):
    content = coordinator_client.get(REGISTER_URL).content.decode()

    assert participant.public_code in content
    assert "49,99" in content or "49.99" in content


def test_register_filters_by_status(coordinator_client, fee, participant):
    paid = coordinator_client.get(f"{REGISTER_URL}?status=paid").content.decode()
    unpaid = coordinator_client.get(f"{REGISTER_URL}?status=unpaid").content.decode()

    assert participant.public_code not in paid
    assert participant.public_code in unpaid


def test_charge_assigns_dues_to_entrants_and_is_idempotent(
    coordinator_client, schedule, participant, elim_stage, edition
):
    StageEntryFactory(participant=participant, stage=elim_stage)

    coordinator_client.post("/coordinator/fees/register/charge/", {"edition": edition.pk})
    coordinator_client.post("/coordinator/fees/register/charge/", {"edition": edition.pk})

    assert ParticipantFee.objects.filter(schedule=schedule, participant=participant).count() == 1


def test_charge_skips_participants_without_an_entry(coordinator_client, schedule, participant, edition):
    """Wpisowe jest opłatą za start: konto bez wpisu do żadnego etapu startem nie jest."""
    coordinator_client.post("/coordinator/fees/register/charge/", {"edition": edition.pk})

    assert ParticipantFee.objects.filter(schedule=schedule).count() == 0


# --- czynności na należności -------------------------------------------------------------------------


def test_reference_is_written_once(coordinator_client, fee):
    coordinator_client.post(f"{REGISTER_URL}{fee.pk}/reference/", {"reference": "P24-1"})
    response = coordinator_client.post(f"{REGISTER_URL}{fee.pk}/reference/", {"reference": "P24-2"})

    fee.refresh_from_db()
    assert response.status_code == 302
    assert fee.external_reference == "P24-1"
    assert AuditLog.objects.filter(action="fee.reference_set").count() == 1


def test_manual_payment_marks_the_due_paid(coordinator_client, fee):
    coordinator_client.post(f"{REGISTER_URL}{fee.pk}/payment/", {"reference": "przelew-7"})

    fee.refresh_from_db()
    assert fee.status == FeeStatus.PAID
    assert fee.paid_at is not None
    assert fee.external_reference == "przelew-7"
    assert AuditLog.objects.filter(action="fee.payment_recorded").exists()


def test_exemption_without_a_reason_changes_nothing(coordinator_client, fee):
    coordinator_client.post(f"{REGISTER_URL}{fee.pk}/exempt/", {"reason": "   "})

    fee.refresh_from_db()
    assert fee.status == FeeStatus.DUE


def test_exemption_and_waiver_are_two_different_decisions(coordinator_client, schedule, edition):
    first = assign_fee(ParticipantFactory(), edition)
    second = assign_fee(ParticipantFactory(), edition)

    coordinator_client.post(f"{REGISTER_URL}{first.pk}/exempt/", {"reason": "uczeń z domu dziecka"})
    coordinator_client.post(f"{REGISTER_URL}{second.pk}/waive/", {"reason": "decyzja komitetu"})

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == FeeStatus.EXEMPT
    assert second.status == FeeStatus.WAIVED


def test_refund_keeps_the_date_of_payment(coordinator_client, fee):
    coordinator_client.post(f"{REGISTER_URL}{fee.pk}/payment/", {"reference": "przelew-7"})
    fee.refresh_from_db()
    paid_at = fee.paid_at

    coordinator_client.post(f"{REGISTER_URL}{fee.pk}/refund/", {"reason": "rezygnacja"})

    fee.refresh_from_db()
    assert fee.status == FeeStatus.REFUNDED
    assert fee.paid_at == paid_at


def test_foreign_due_is_404(client_for, competition, other_competition):
    enable(other_competition, FEATURE)
    from apps.competitions.tests.factories import EditionFactory

    foreign_edition = EditionFactory(competition=other_competition)
    create_fee_schedule(other_competition, edition=foreign_edition, name="Cudze", amount=Decimal("10.00"))
    foreign = assign_fee(ParticipantFactory(competition=other_competition), foreign_edition)
    enable(competition, FEATURE)
    client, _user = coordinator_for(client_for, competition)

    response = client.post(f"{REGISTER_URL}{foreign.pk}/payment/", {"reference": "x"})

    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.status == FeeStatus.DUE


# --- dokument rozliczeniowy --------------------------------------------------------------------------


def test_document_without_a_template_is_refused_with_an_explanation(coordinator_client, fee):
    """Rachunek bez zapamiętanej wersji szablonu byłby dokumentem, którego nie da się odtworzyć."""
    response = coordinator_client.get(f"{REGISTER_URL}{fee.pk}/document/")

    fee.refresh_from_db()
    assert response.status_code == 302
    assert fee.document_version == ""


def test_document_is_issued_and_downloaded(coordinator_client, competition, fee):
    enable(competition, DOCUMENTS_FLAG)
    set_current_template(
        competition,
        DocumentKind.INVOICE,
        version="1.0 z 1 marca 2027",
        title="Rachunek za wpisowe",
        statement="Do zapłaty {amount} {currency} w terminie do {due_date}.",
        signature_line="Skarbnik komitetu",
    )

    response = coordinator_client.get(f"{REGISTER_URL}{fee.pk}/document/")

    fee.refresh_from_db()
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert fee.document_version == "1.0 z 1 marca 2027"
    assert AuditLog.objects.filter(action="fee.document_issued").exists()


def test_document_filename_carries_no_surname(coordinator_client, competition, fee, participant):
    enable(competition, DOCUMENTS_FLAG)
    set_current_template(
        competition,
        DocumentKind.INVOICE,
        version="1.0",
        title="Rachunek",
        statement="Do zapłaty {amount} {currency}.",
    )

    response = coordinator_client.get(f"{REGISTER_URL}{fee.pk}/document/")

    disposition = response["Content-Disposition"]
    assert participant.user.last_name not in disposition
    assert participant.public_code in disposition
