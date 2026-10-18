"""Automatyczny zapis nowych kont do etapu treningowego i „Zapisz wszystkich” (REG-UX-01 § 3)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.bulk_registration import accept_invitation
from apps.accounts.models import Participant, Voivodeship
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.accounts.tests.test_bulk_registration import consents, invited_participant
from apps.competitions import auto_registration
from apps.competitions.auto_registration import (
    register_all_current,
    register_new_participant,
    set_auto_registration,
)
from apps.competitions.models import StageEntry, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def training():
    stage = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.TRAINING, name="Trening")
    stage.auto_register_new_participants = True
    stage.save(update_fields=["auto_register_new_participants"])
    return stage


def entries(stage) -> set[int]:
    return set(StageEntry.objects.filter(stage=stage).values_list("participant_id", flat=True))


# --- nowe konto -----------------------------------------------------------------------------------


def test_nowe_konto_ze_zgoda_zapisuje_sie_do_treningu_po_zatwierdzeniu(
    training, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        participant = ParticipantFactory()

    assert callbacks, "zapis idzie po zatwierdzeniu transakcji, a nie w jej środku"
    assert entries(training) == {participant.pk}


def test_wylaczony_przelacznik_niczego_nie_zapisuje(training, django_capture_on_commit_callbacks):
    set_auto_registration(training, False)

    with django_capture_on_commit_callbacks(execute=True):
        ParticipantFactory()

    assert entries(training) == set()


def test_rejestracja_formularzem_zapisuje_do_treningu(training, django_capture_on_commit_callbacks):
    from django.test import Client

    from apps.email_delivery.tests.test_forms import REGISTER_URL, _payload

    with django_capture_on_commit_callbacks(execute=True):
        response = Client().post(REGISTER_URL, _payload(email="uczen@szkola.test"))

    assert response.status_code == 302
    participant = Participant.objects.get(user__email="uczen@szkola.test")
    assert entries(training) == {participant.pk}


def test_konto_z_importu_zapisuje_sie_dopiero_po_przyjeciu_zaproszenia(
    training, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        participant = invited_participant()
    # Profil bez zgód nie może się zapisać (CONSENTS_MISSING) – i nie próbujemy.
    assert entries(training) == set()

    with django_capture_on_commit_callbacks(execute=True):
        accept_invitation(
            participant,
            password="Poprawne-Haslo-2026",
            phone="600 100 200",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(),
        )

    assert entries(training) == {participant.pk}


def test_ponowna_zgoda_nie_jest_nowym_kontem(training, django_capture_on_commit_callbacks):
    participant = ParticipantFactory()  # bez wykonania callbacków – konto „sprzed przełącznika”
    participant.gdpr_consent_at = timezone.now()

    with django_capture_on_commit_callbacks(execute=True):
        participant.save(update_fields=["gdpr_consent_at"])

    assert entries(training) == set()


def test_zapis_jest_idempotentny(training, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        participant = ParticipantFactory()

    assert register_new_participant(participant.pk) == []
    assert StageEntry.objects.filter(stage=training, participant=participant).count() == 1


def test_blad_zapisu_nie_blokuje_rejestracji(
    training, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    def broken(*args, **kwargs):
        raise RuntimeError("awaria")

    monkeypatch.setattr(auto_registration, "register_for_stage", broken)

    with django_capture_on_commit_callbacks(execute=True):
        participant = ParticipantFactory()

    assert Participant.objects.filter(pk=participant.pk).exists()
    assert entries(training) == set()
    assert "nie powiódł się" in caplog.text


def test_zamkniety_etap_i_edycja_archiwalna_sa_pomijane(training, django_capture_on_commit_callbacks):
    training.opens_at = timezone.now() + timedelta(days=1)
    training.deadline_at = training.opens_at + timedelta(days=14)
    training.save(update_fields=["opens_at", "deadline_at"])

    with django_capture_on_commit_callbacks(execute=True):
        ParticipantFactory()

    assert entries(training) == set()


def test_konto_innego_konkursu_nie_trafia_do_cudzego_treningu(
    training, other_competition, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        ParticipantFactory(competition=other_competition)

    assert entries(training) == set()


# --- przełącznik ------------------------------------------------------------------------------


def test_przelacznik_tylko_dla_treningu_i_z_audytem():
    elim = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)
    with pytest.raises(DomainError):
        set_auto_registration(elim, True)

    stage = StageFactory(edition=elim.edition, kind=StageKind.TRAINING)
    set_auto_registration(stage, True)
    set_auto_registration(stage, True)  # bez zmiany – bez drugiego wpisu

    stage.refresh_from_db()
    assert stage.auto_register_new_participants is True
    assert AuditLog.objects.filter(action="stage.auto_registration_changed").count() == 1


def test_wiez_w_bazie_nie_wpuszcza_przelacznika_poza_treningiem():
    stage = StageFactory(edition=CurrentEditionFactory(), kind=StageKind.ELIM)
    stage.auto_register_new_participants = True
    with pytest.raises(IntegrityError), transaction.atomic():
        stage.save(update_fields=["auto_register_new_participants"])


# --- zapisz wszystkich ------------------------------------------------------------------------


def test_zapisz_wszystkich_obecnych_z_raportem_i_audytem(training, other_competition):
    zapisany = ParticipantFactory()
    StageEntry.objects.create(participant=zapisany, stage=training)
    nowy = ParticipantFactory()
    ParticipantFactory(gdpr_consent_at=None)  # zaproszony, jeszcze bez zgód
    ParticipantFactory(user=UserFactory(is_active=False, email_verified_at=None))
    ParticipantFactory(competition=other_competition)  # cudzy konkurs – nie liczy się wcale

    summary = register_all_current(training)

    assert summary == {"registered": 1, "already": 1, "skipped": 2}
    assert entries(training) == {zapisany.pk, nowy.pk}
    assert AuditLog.objects.get(action="stage.bulk_registered").diff == summary
    # Drugi raz – nic do zrobienia, nikt nie dostaje drugiego wpisu.
    assert register_all_current(training) == {"registered": 0, "already": 2, "skipped": 2}


def test_zapisz_wszystkich_tylko_w_otwartym_treningu(training):
    elim = StageFactory(edition=training.edition, kind=StageKind.ELIM)
    with pytest.raises(DomainError):
        register_all_current(elim)

    training.opens_at = timezone.now() + timedelta(days=1)
    training.deadline_at = training.opens_at + timedelta(days=14)
    training.save(update_fields=["opens_at", "deadline_at"])
    ParticipantFactory()
    with pytest.raises(DomainError):
        register_all_current(training)
    assert entries(training) == set()
