"""Wpisowe (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.1) – cennik, rejestr należności, dokument.

Test pilnuje trzech rzeczy naraz i tylko razem mają one sens:

- **Konkurs #1 nie zauważa niczego.** Flaga ``fees`` jest wyłączona, obie tabele są puste, a każde
  wejście do modułu schodzi do pustki albo do „nie blokuje” **bez zapytania do bazy**. To jest
  wymaganie nadrzędne (§ 0.1) i dlatego stoi tu jako osobna grupa asercji, a nie jako komentarz.
- **Konkurs drugi dostaje wpisowe, którego pierwszy nie widzi.** Cennik ma własną kolumnę
  konkursu, należność dochodzi do niego przez uczestnika – obie drogi są sprawdzalne na poziomie
  queryseta (§ 5.7).
- **Rejestr odpowiada na pytania, które padają:** ile się należy, czy wpłynęło, kiedy, z jakiej
  wersji dokumentu powstał rachunek. Decyzje organizatora D15 (rejestr, nie księgowość) i D16
  (brak wpłaty domyślnie niczego nie blokuje) mają tu po własnej grupie testów, bo obie są
  odpowiedzią na pytanie „czego system **nie** robi”, a takich reguł nie pilnuje nic poza testem.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import Category
from apps.competitions.tests.factories import EditionFactory
from apps.core.api import DomainError
from apps.tenancy.fees import (
    FeeSchedule,
    FeeStatus,
    ParticipantFee,
    assign_fee,
    assign_fee_if_due,
    create_fee_schedule,
    fee_document_context,
    fee_for,
    fee_rows,
    fee_totals,
    fees_enabled,
    fees_for,
    find_fee_by_reference,
    issue_fee_document,
    mark_exempt,
    quantize_money,
    record_fee_document,
    record_payment,
    record_refund,
    require_fees,
    schedule_for,
    schedules_for,
    update_fee_schedule,
    waive_fee,
)

pytestmark = pytest.mark.django_db

#: Kwota wpisowego w testach. Liczba z groszami, a nie okrągła: zaokrąglenie jest tu regułą, a nie
#: szczegółem – rejestr ma oddawać dokładnie to, co w nim postawiono.
AMOUNT = Decimal("49.99")


def with_fees(competition):
    """Włącza konkursowi flagę ``fees``. Jedyna droga do wpisowego – i to jest przedmiot testu."""
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def paid_competition(other_competition):
    """Konkurs #2 z włączonym wpisowem, jedną edycją i jednym uczestnikiem.

    Zamiast fabryki: ``apps/tenancy/tests/factories.py`` jest plikiem wspólnym, a ten test jest
    jedynym czytelnikiem wpisowego do czasu montażu wydania K (T42).
    """
    with_fees(other_competition)
    edition = EditionFactory(competition=other_competition)
    participant = ParticipantFactory(competition=other_competition)
    return other_competition, edition, participant


def make_schedule(competition, edition, **kwargs):
    kwargs.setdefault("name", "Wpisowe podstawowe")
    kwargs.setdefault("amount", AMOUNT)
    return create_fee_schedule(competition, edition=edition, **kwargs)


# --- Konkurs #1: nic się nie zmieniło ---------------------------------------------------------------


def test_fees_flag_is_off_for_competition_one(competition):
    """Flaga jest w katalogu (T8) i domyślnie wyłączona – Olimpiada Kwantowa jest bezpłatna."""
    assert competition.has_feature("fees") is False
    assert fees_enabled(competition) is False


def test_competition_one_has_no_fee_rows(competition):
    """Obie tabele Konkursu #1 są puste: migracja nie miała ich czym wypełnić."""
    assert not FeeSchedule.objects.for_competition(competition).exists()
    assert not ParticipantFee.objects.for_competition(competition).exists()


def test_reads_cost_nothing_without_the_flag(competition, django_assert_num_queries):
    """Bez flagi nie pada **ani jedno** zapytanie – budżety z § 5.6 są bramką, nie zaleceniem."""
    with django_assert_num_queries(0):
        assert list(schedules_for(competition)) == []
        assert list(fees_for(competition)) == []
        assert find_fee_by_reference(competition, "PLN-1") is None


def test_schedule_for_is_none_without_the_flag(competition, django_assert_num_queries):
    """Dobór cennika dla konkursu bez wpisowego kończy się na fladze, a nie na zapytaniu."""
    edition = EditionFactory(competition=competition)
    with django_assert_num_queries(0):
        assert schedule_for(edition) is None


def test_unresolved_competition_means_no_fees():
    """„Nie wiadomo, o który konkurs chodzi” znaczy „nie pobiera wpisowego” (§ 1.0 (b))."""
    assert fees_enabled(None) is False


def test_writes_are_refused_without_the_flag(competition):
    """Zapis w konkursie bez wpisowego jest błędem wołającego i ma być słyszalny od razu."""
    with pytest.raises(DomainError) as excinfo:
        require_fees(competition)
    assert excinfo.value.machine_code == "FEES_DISABLED"


def test_soft_assignment_is_silent_without_the_flag(competition):
    """Rejestracja biegnie w każdym konkursie i nie ma powodu wiedzieć, czy ten coś pobiera."""
    edition = EditionFactory(competition=competition)
    participant = ParticipantFactory(competition=competition)
    assert assign_fee_if_due(participant, edition) is None
    assert fee_for(participant, edition) is None


def test_panel_summaries_are_empty_without_the_flag(competition):
    edition = EditionFactory(competition=competition)
    assert fee_rows(edition) == []
    assert fee_totals(edition) == {}


# --- izolacja (§ 5.7) --------------------------------------------------------------------------------


def test_schedules_of_a_are_invisible_to_b(competition, other_competition):
    """Cennik konkursu A nie pokazuje się w zakresowaniu konkursu B – i odwrotnie."""
    _, edition, _ = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    assert schedule in FeeSchedule.objects.for_competition(other_competition)
    assert not FeeSchedule.objects.for_competition(competition).filter(pk=schedule.pk).exists()


def test_fees_of_a_are_invisible_to_b(competition, other_competition):
    """Należność dochodzi do konkursu przez uczestnika i tą samą drogą jest odcinana."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    assert fee in ParticipantFee.objects.for_competition(other_competition)
    assert not ParticipantFee.objects.for_competition(competition).filter(pk=fee.pk).exists()


def test_payment_reference_is_not_matched_across_competitions(competition, other_competition):
    """Dopasowanie wpłaty po identyfikatorze jest zakresowane – inaczej webhook byłby wyciekiem."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    record_payment(fee, external_reference="TR-1")
    assert find_fee_by_reference(other_competition, "TR-1") == fee
    assert find_fee_by_reference(competition, "TR-1") is None


def test_schedule_of_a_foreign_edition_is_refused(other_competition):
    """Cennik, edycja i kategoria muszą należeć do jednego konkursu (§ 3.4)."""
    _, _, _ = paid_competition(other_competition)
    foreign_edition = EditionFactory()
    with pytest.raises(ValidationError) as excinfo:
        make_schedule(other_competition, foreign_edition)
    assert "edition" in excinfo.value.message_dict


# --- cennik -------------------------------------------------------------------------------------------


def test_amount_is_quantized_to_two_places(other_competition):
    """Kwota trafia do rejestru zaokrąglona tak, jak ją widzi kolumna – a nie „prawie tak”."""
    _, edition, _ = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition, amount="50.005")
    assert schedule.amount == Decimal("50.01")
    assert quantize_money("7") == Decimal("7.00")


def test_currency_must_be_an_iso_code(other_competition):
    _, edition, _ = paid_competition(other_competition)
    with pytest.raises(ValidationError) as excinfo:
        make_schedule(other_competition, edition, currency="pln")
    assert "currency" in excinfo.value.message_dict


def test_negative_amount_is_refused_by_the_database(other_competition):
    """Ostatnia linia obrony: więz bazy łapie zapis z pominięciem ``full_clean``."""
    _, edition, _ = paid_competition(other_competition)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeSchedule.objects.create(
            competition=other_competition, edition=edition, name="Ujemne", amount=Decimal("-1.00")
        )


def test_edition_has_at_most_one_base_schedule(other_competition):
    """Cena podstawowa edycji jest jedna – ``NULL`` w więzie unikalności sam tego nie pilnuje."""
    _, edition, _ = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeSchedule.objects.create(
            competition=other_competition, edition=edition, name="Druga", amount=AMOUNT
        )


def test_edition_has_at_most_one_schedule_per_category(other_competition):
    _, edition, _ = paid_competition(other_competition)
    category = Category.objects.create(competition=other_competition, code="podstawowa", name="Podstawowa")
    make_schedule(other_competition, edition, category=category)
    with pytest.raises(IntegrityError), transaction.atomic():
        FeeSchedule.objects.create(
            competition=other_competition,
            edition=edition,
            category=category,
            name="Druga",
            amount=AMOUNT,
        )


def test_schedule_resolution_goes_from_detail_to_general(other_competition):
    """``(edycja, kategoria)`` → ``(edycja, —)`` → brak. Tak samo, jak dobiera się szablon (§ 1.1.3)."""
    _, edition, _ = paid_competition(other_competition)
    assert schedule_for(edition) is None
    base = make_schedule(other_competition, edition)
    category = Category.objects.create(competition=other_competition, code="starsza", name="Starsza")
    assert schedule_for(edition, category) == base
    specific = make_schedule(
        other_competition, edition, category=category, name="Wpisowe kategorii", amount=Decimal("10.00")
    )
    assert schedule_for(edition, category) == specific
    assert schedule_for(edition) == base


def test_withdrawn_schedule_stops_charging_but_stays_in_the_register(other_competition):
    """Wycofany cennik nie nalicza nowych należności, a stare dalej z niego wynikają."""
    _, edition, participant = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    update_fee_schedule(schedule, is_active=False)
    assert schedule_for(edition) is None
    fee.refresh_from_db()
    assert fee.schedule_id == schedule.pk


# --- naliczanie należności -----------------------------------------------------------------------------


def test_assignment_copies_amount_currency_and_term(other_competition):
    """Kwota, waluta i termin są kopią z chwili naliczenia – to jest cała reguła § 1.5.1."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition, due_days=7)
    today = timezone.localdate()
    fee = assign_fee(participant, edition, issued_on=today)
    assert fee.amount == AMOUNT
    assert fee.currency == "PLN"
    assert fee.status == FeeStatus.DUE
    assert fee.due_on == today + timedelta(days=7)


def test_assignment_is_idempotent(other_competition):
    """Rejestracja, import i panel wołają to samo – druga próba nie wystawia drugiej należności."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    first = assign_fee(participant, edition)
    second = assign_fee(participant, edition)
    assert first.pk == second.pk
    assert ParticipantFee.objects.filter(participant=participant).count() == 1


def test_price_change_does_not_touch_issued_dues(other_competition):
    """Podwyżka obowiązuje od następnego naliczenia, a nie wstecz – ktoś już to zapłacił."""
    _, edition, participant = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    update_fee_schedule(schedule, amount=Decimal("99.00"))
    fee.refresh_from_db()
    assert fee.amount == AMOUNT


def test_assignment_without_a_schedule_is_a_conflict(other_competition):
    """Konkurs z wpisowem, ale bez cennika na ten rocznik: wołający ma to usłyszeć."""
    _, edition, participant = paid_competition(other_competition)
    with pytest.raises(DomainError) as excinfo:
        assign_fee(participant, edition)
    assert excinfo.value.machine_code == "FEE_SCHEDULE_MISSING"
    assert assign_fee_if_due(participant, edition) is None


def test_one_due_per_participant_and_schedule(other_competition):
    _, edition, participant = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    assign_fee(participant, edition)
    with pytest.raises(IntegrityError), transaction.atomic():
        ParticipantFee.objects.create(
            schedule=schedule, participant=participant, amount=AMOUNT, currency="PLN"
        )


# --- wpłaty, zwolnienia, zwroty --------------------------------------------------------------------------


def test_payment_sets_status_and_date(other_competition):
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = record_payment(assign_fee(participant, edition), external_reference="TR-7")
    assert fee.status == FeeStatus.PAID
    assert fee.paid_at is not None
    assert fee.is_settled is True


def test_repeated_delivery_changes_nothing(other_competition):
    """Idempotencja webhooka (T49): powtórzone doręczenie nie przestawia daty wpłaty."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = record_payment(assign_fee(participant, edition), external_reference="TR-7")
    first_paid_at = fee.paid_at
    again = record_payment(fee, external_reference="TR-7")
    assert again.paid_at == first_paid_at


def test_second_reference_on_a_paid_due_is_a_conflict(other_competition):
    """Druga wpłata tej samej należności jest sprawą dla człowieka, a nie dla automatu."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = record_payment(assign_fee(participant, edition), external_reference="TR-7")
    with pytest.raises(DomainError) as excinfo:
        record_payment(fee, external_reference="TR-8")
    assert excinfo.value.machine_code == "FEE_ALREADY_PAID"


def test_paid_status_requires_a_date_in_the_database(other_competition):
    """„Zapłacone” bez odpowiedzi na pytanie „kiedy” nie przechodzi przez więz bazy."""
    _, edition, participant = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    with pytest.raises(IntegrityError), transaction.atomic():
        ParticipantFee.objects.create(
            schedule=schedule,
            participant=participant,
            amount=AMOUNT,
            currency="PLN",
            status=FeeStatus.PAID,
        )


def test_exemption_requires_a_reason(other_competition):
    _, edition, participant = paid_competition(other_competition)
    schedule = make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    with pytest.raises(DomainError) as excinfo:
        mark_exempt(fee, reason="   ")
    assert excinfo.value.machine_code == "FEE_REASON_REQUIRED"
    with pytest.raises(IntegrityError), transaction.atomic():
        ParticipantFee.objects.create(
            schedule=schedule,
            participant=ParticipantFactory(competition=other_competition),
            amount=AMOUNT,
            currency="PLN",
            status=FeeStatus.EXEMPT,
        )


def test_exemption_and_waiver_are_two_different_decisions(other_competition):
    """Regulaminowe zwolnienie i uznaniowe umorzenie to dwa różne pytania w sprawozdaniu."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    exempt = mark_exempt(assign_fee(participant, edition), reason="Uczestnik zwolniony regulaminem.")
    assert exempt.status == FeeStatus.EXEMPT
    assert exempt.is_settled is True

    other_participant = ParticipantFactory(competition=other_competition)
    waived = waive_fee(assign_fee(other_participant, edition), reason="Decyzja organizatora.")
    assert waived.status == FeeStatus.WAIVED
    assert waived.is_settled is True


def test_payment_is_refused_on_a_settled_due(other_competition):
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = mark_exempt(assign_fee(participant, edition), reason="Zwolnienie.")
    with pytest.raises(DomainError) as excinfo:
        record_payment(fee, external_reference="TR-9")
    assert excinfo.value.machine_code == "FEE_NOT_DUE"


def test_refund_keeps_the_date_of_payment(other_competition):
    """Zwrot jest drugim faktem obok wpłaty, a nie wymazaniem pierwszego."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = record_payment(assign_fee(participant, edition), external_reference="TR-7")
    paid_at = fee.paid_at
    refunded = record_refund(fee, reason="Rezygnacja przed etapem.")
    assert refunded.status == FeeStatus.REFUNDED
    assert refunded.paid_at == paid_at
    assert refunded.is_settled is False


def test_refund_is_refused_on_an_unpaid_due(other_competition):
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    with pytest.raises(DomainError) as excinfo:
        record_refund(assign_fee(participant, edition))
    assert excinfo.value.machine_code == "FEE_NOT_PAID"


# --- podsumowania dla panelu (T42) ----------------------------------------------------------------------


def test_rows_and_totals_are_grouped_by_currency(other_competition):
    """Sumowanie złotówek z euro dałoby liczbę, która nie znaczy nic (D15)."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    record_payment(assign_fee(participant, edition), external_reference="TR-7")
    second = ParticipantFactory(competition=other_competition)
    assign_fee(second, edition)

    rows = fee_rows(edition)
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {FeeStatus.PAID, FeeStatus.DUE}

    totals = fee_totals(edition)
    assert totals["PLN"]["count"] == 2
    assert totals["PLN"]["charged"] == AMOUNT * 2
    assert totals["PLN"]["paid"] == AMOUNT
    assert totals["PLN"]["outstanding"] == AMOUNT


def test_rows_do_not_grow_with_queries(other_competition, django_assert_num_queries):
    """Lista należności ma nie rosnąć zapytaniami razem z liczbą uczestników (§ 5.6)."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    assign_fee(participant, edition)
    with django_assert_num_queries(1):
        fee_rows(edition)
    for _index in range(3):
        assign_fee(ParticipantFactory(competition=other_competition), edition)
    with django_assert_num_queries(1):
        assert len(fee_rows(edition)) == 4


# --- dokument rozliczeniowy (D15, hak do T12) ---------------------------------------------------------------


class FakeDocument:
    """Zaślepka składacza dokumentów: tyle, ile ten moduł ma prawo o nim wiedzieć."""

    def __init__(self, version: str):
        self.version = version


def test_document_version_is_remembered(other_competition):
    """PDF-a nie przechowujemy, więc bez wersji szablonu rachunek nie dałby się odtworzyć."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    assert fee.document_version == ""
    record_fee_document(fee, FakeDocument("2.0 (2026)"))
    fee.refresh_from_db()
    assert fee.document_version == "2.0 (2026)"
    assert fee.document_issued_at is not None


def test_document_version_may_be_given_as_a_string(other_competition):
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = record_fee_document(assign_fee(participant, edition), "1.0")
    assert fee.document_version == "1.0"


def test_document_without_a_version_is_refused(other_competition):
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    with pytest.raises(DomainError) as excinfo:
        record_fee_document(assign_fee(participant, edition), FakeDocument(""))
    assert excinfo.value.machine_code == "FEE_DOCUMENT_VERSION_MISSING"


def test_renderer_is_injected_not_imported(other_competition):
    """Umowa z modułem dokumentów: ``renderer(konkurs, "INVOICE", **podstawienia)``.

    Kształt wywołania jest dokładnie taki, jak ``apps.tenancy.documents.render_document``, żeby
    ekran koordynatora (T42) podał je tu wprost, bez przejściówki – ale **import** zostaje po
    stronie wołającego, a nie tutaj.
    """
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    seen: dict[str, object] = {}

    def renderer(competition, kind, **context):
        seen.update({"competition": competition, "kind": kind, "context": context})
        return FakeDocument("3.1")

    rendered = issue_fee_document(fee, renderer)
    assert rendered.version == "3.1"
    assert seen["competition"] == other_competition
    assert seen["kind"] == "INVOICE"
    assert seen["context"]["amount"] == "49.99"
    fee.refresh_from_db()
    assert fee.document_version == "3.1"


def test_document_context_carries_what_a_receipt_needs(other_competition):
    """Znaczniki wspólne z § 1.1.3 plus cztery, których dyplom nie zna (kwota, waluta, termin, VAT).

    Nazwa konkursu i organizator **nie** są w kontekście: wynikają z konkursu, a nie z należności,
    i wylicza je moduł dokumentów.
    """
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition, vat_rate=23)
    context = fee_document_context(assign_fee(participant, edition))
    assert "competition" not in context
    assert "organizer" not in context
    assert set(context) >= {
        "edition",
        "participant_code",
        "recipient",
        "school",
        "date",
        "number",
        "amount",
        "currency",
        "due_date",
        "vat_rate",
    }
    assert context["participant_code"] == participant.public_code
    assert context["edition"] == edition.year_label
    assert context["vat_rate"] == "23"


def test_reference_is_not_an_invoice_number(other_competition):
    """D15: system nie numeruje faktur ustawowo – oznaczenie jest kluczem do wiersza rejestru."""
    _, edition, participant = paid_competition(other_competition)
    make_schedule(other_competition, edition)
    fee = assign_fee(participant, edition)
    assert fee.reference == f"{participant.public_code}/{fee.pk}"
