"""``StageEntry.fee`` i bramka z decyzji D16 (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.1).

Pole na wpisie do etapu jest **skrótem** do należności uczestnika za tę edycję – istnieje po to,
żeby ekran etapu pokazał „nieopłacone” bez złączenia przez edycję. Test pilnuje dwóch rzeczy:

- **Konkurs #1 nie zauważa niczego:** kolumna jest ``NULL``, nikt jej nie ustawia, a pytanie
  „czy brak wpłaty blokuje oddanie pracy” kończy się odpowiedzią „nie” **bez zapytania do bazy**.
- **Bramka jest opcją organizatora, a nie zachowaniem systemu (D16):** nawet w konkursie
  z wpisowem nieopłacona należność niczego nie blokuje, dopóki cennik nie ma
  ``blocks_submission=True``. Przelew idzie dwa dni, a deadline na niego nie czeka.

Moduł należności stoi w ``apps/tenancy/fees.py``; tutaj sprawdzamy wyłącznie to, co widzi domena
zawodów – czyli kolumnę przy wpisie i jedną funkcję odpowiadającą na jedno pytanie.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntry
from apps.tenancy.fees import (
    assign_fee,
    attach_fee,
    create_fee_schedule,
    mark_exempt,
    record_payment,
    submission_blocked,
)

from .factories import StageEntryFactory, StageFactory

pytestmark = pytest.mark.django_db

AMOUNT = Decimal("50.00")


def loaded(entry):
    """Wpis pobrany z relacjami, których czyta bramka – żeby liczyć **jej** zapytania, a nie cudze."""
    return StageEntry.objects.select_related("stage__edition__competition").get(pk=entry.pk)


def paying_entry(competition, *, blocks_submission: bool):
    """Wpis do etapu w konkursie z wpisowem: cennik, należność, dowiązanie do wpisu."""
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    stage = StageFactory(competition=competition)
    participant = ParticipantFactory(competition=competition)
    entry = StageEntryFactory(participant=participant, stage=stage)
    create_fee_schedule(
        competition,
        edition=stage.edition,
        name="Wpisowe",
        amount=AMOUNT,
        blocks_submission=blocks_submission,
    )
    fee = assign_fee(participant, stage.edition)
    attach_fee(entry, fee)
    entry.refresh_from_db()
    return entry, fee


# --- Konkurs #1: nic się nie zmieniło ------------------------------------------------------------------


def test_stage_entry_has_no_fee_by_default(competition):
    """Wpis powstaje bez należności i nikt mu jej nie dopisuje – migracja niczego nie wypełniła."""
    entry = StageEntryFactory(competition=competition)
    assert entry.fee_id is None


def test_gate_is_open_without_the_flag(competition, django_assert_num_queries):
    """Bez flagi ``fees`` bramka odpowiada „nie blokuje” i nie pyta bazy ani razu (§ 5.6)."""
    entry = loaded(StageEntryFactory(competition=competition))
    with django_assert_num_queries(0):
        assert submission_blocked(entry) is False


def test_attaching_a_fee_does_nothing_without_the_flag(competition):
    """Dowiązanie w konkursie bez wpisowego jest bezczynne, a nie błędne: nie ma czego wiązać."""
    entry = StageEntryFactory(competition=competition)
    assert attach_fee(entry) is None
    entry.refresh_from_db()
    assert entry.fee_id is None


# --- decyzja D16: bramka jest opcją, a nie zachowaniem systemu ---------------------------------------------


def test_unpaid_fee_does_not_block_by_default(other_competition):
    """Domyślnie (b)rak wpłaty niczego nie blokuje – to jest cała treść decyzji D16."""
    entry, fee = paying_entry(other_competition, blocks_submission=False)
    assert entry.fee_id == fee.pk
    assert submission_blocked(loaded(entry)) is False


def test_unpaid_fee_blocks_when_the_organiser_opted_in(other_competition):
    entry, _fee = paying_entry(other_competition, blocks_submission=True)
    assert submission_blocked(loaded(entry)) is True


def test_payment_opens_the_gate(other_competition):
    entry, fee = paying_entry(other_competition, blocks_submission=True)
    record_payment(fee, external_reference="TR-1")
    assert submission_blocked(loaded(entry)) is False


def test_exemption_opens_the_gate(other_competition):
    """Zwolniony nie ma czego wpłacić, więc bramka nie ma czego pilnować."""
    entry, fee = paying_entry(other_competition, blocks_submission=True)
    mark_exempt(fee, reason="Zwolnienie regulaminowe.")
    assert submission_blocked(loaded(entry)) is False


def test_gate_finds_the_fee_without_the_shortcut(other_competition):
    """Puste ``StageEntry.fee`` nie jest luką: bramka dochodzi do należności po uczestniku i edycji."""
    entry, _fee = paying_entry(other_competition, blocks_submission=True)
    StageEntry.objects.filter(pk=entry.pk).update(fee=None)
    assert submission_blocked(loaded(entry)) is True
