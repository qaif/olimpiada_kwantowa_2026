"""Blok „Zapis do treningu” na ekranie etapu koordynatora (REG-UX-01 § 3.3)."""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntry, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def training():
    return StageFactory(edition=CurrentEditionFactory(), kind=StageKind.TRAINING, name="Trening")


def edit_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/edit/"


def test_blok_jest_tylko_na_ekranie_etapu_treningowego(web_client, coordinator, training):
    elim = StageFactory(edition=training.edition, kind=StageKind.ELIM)
    web_client.force_login(coordinator)

    body = web_client.get(edit_url(training)).content.decode()
    assert "Zapis do treningu" in body
    assert f'action="/coordinator/stages/{training.pk}/training/register-all/"' in body
    assert 'x-data="confirmSubmit"' in body  # potwierdzenie bez inline JS

    assert "Zapis do treningu" not in web_client.get(edit_url(elim)).content.decode()


def test_przelacznik_wlacza_i_wylacza(web_client, coordinator, training):
    web_client.force_login(coordinator)
    url = f"/coordinator/stages/{training.pk}/training/auto/"

    response = web_client.post(url, {"enabled": "1"})
    assert response.status_code == 302
    assert response.headers["Location"] == edit_url(training)
    training.refresh_from_db()
    assert training.auto_register_new_participants is True
    assert "włączony" in web_client.get(edit_url(training)).content.decode()

    web_client.post(url, {})
    training.refresh_from_db()
    assert training.auto_register_new_participants is False
    assert AuditLog.objects.filter(action="stage.auto_registration_changed").count() == 2


def test_zapisz_wszystkich_z_raportem(web_client, coordinator, training):
    participants = [ParticipantFactory(), ParticipantFactory()]
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{training.pk}/training/register-all/")

    assert response.status_code == 302
    assert set(StageEntry.objects.filter(stage=training).values_list("participant_id", flat=True)) == {
        participant.pk for participant in participants
    }
    messages = [str(message) for message in web_client.get(response.headers["Location"]).context["messages"]]
    assert messages[0].startswith("Zapis do etapu Trening: zapisano 2, już zapisanych 0, pominięto 0")


def test_etap_inny_niz_treningowy_dostaje_komunikat_bledu(web_client, coordinator, training):
    elim = StageFactory(edition=training.edition, kind=StageKind.ELIM)
    ParticipantFactory()
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/stages/{elim.pk}/training/register-all/")

    assert response.status_code == 302
    assert not StageEntry.objects.filter(stage=elim).exists()
    messages = [str(message) for message in web_client.get(response.headers["Location"]).context["messages"]]
    assert messages == ["Automatyczny i zbiorczy zapis działa wyłącznie dla etapu treningowego."]


def test_etap_cudzego_konkursu_to_404(web_client, coordinator, other_competition):
    foreign = StageFactory(competition=other_competition, kind=StageKind.TRAINING)
    web_client.force_login(coordinator)

    for suffix in ("auto/", "register-all/"):
        assert (
            web_client.post(
                f"/coordinator/stages/{foreign.pk}/training/{suffix}", {"enabled": "1"}
            ).status_code
            == 404
        )
    foreign.refresh_from_db()
    assert foreign.auto_register_new_participants is False


def test_uczestnik_nie_ma_dostepu(web_client, participant, training):
    web_client.force_login(participant.user)

    response = web_client.post(f"/coordinator/stages/{training.pk}/training/auto/", {"enabled": "1"})

    assert response.status_code == 403
    training.refresh_from_db()
    assert training.auto_register_new_participants is False
