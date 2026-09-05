"""T-03, kryteria 7-8: API odczytu zawodów i rejestracja do etapu przez HTTP."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.competitions.models import StageEntry, StageEntryStatus, StageKind

from .factories import CurrentEditionFactory, EditionFactory, ProblemFactory, StageEntryFactory, StageFactory

CURRENT_EDITION_URL = "/api/competitions/editions/current/"
MY_ENTRIES_URL = "/api/competitions/me/entries/"


def _register_url(stage) -> str:
    return f"/api/competitions/stages/{stage.id}/register/"


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_kryterium_7_druga_rejestracja_przez_api_daje_409_already_registered(api):
    """7. `POST stages/{id}/register/` drugi raz → 409 ALREADY_REGISTERED."""
    stage = StageFactory(kind=StageKind.ELIM, edition=CurrentEditionFactory())
    participant = ParticipantFactory()
    api.force_authenticate(user=participant.user)

    first = api.post(_register_url(stage))
    second = api.post(_register_url(stage))

    assert first.status_code == 201, first.data
    assert first.json()["status"] == StageEntryStatus.REGISTERED
    assert second.status_code == 409
    assert second.json()["code"] == "ALREADY_REGISTERED"
    assert StageEntry.objects.filter(participant=participant, stage=stage).count() == 1


@pytest.mark.django_db
def test_rejestracja_do_etapu_okregowego_przez_api_daje_403(api):
    """Ręczna rejestracja do DISTRICT → 403 STAGE_NOT_OPEN_FOR_REGISTRATION."""
    stage = StageFactory(kind=StageKind.DISTRICT)
    api.force_authenticate(user=ParticipantFactory().user)

    resp = api.post(_register_url(stage))

    assert resp.status_code == 403
    assert resp.json()["code"] == "STAGE_NOT_OPEN_FOR_REGISTRATION"


@pytest.mark.django_db
def test_rejestracja_wymaga_roli_uczestnika(api):
    """Test negatywny uprawnień: anonim → 401, recenzent i koordynator → 403."""
    stage = StageFactory(kind=StageKind.ELIM)

    assert api.post(_register_url(stage)).status_code == 401

    api.force_authenticate(user=ActiveReviewerFactory().user)
    assert api.post(_register_url(stage)).status_code == 403

    api.force_authenticate(user=CoordinatorFactory())
    assert api.post(_register_url(stage)).status_code == 403
    assert StageEntry.objects.count() == 0


@pytest.mark.django_db
def test_kryterium_8_biezaca_edycja_bez_logowania_zwraca_etapy(api):
    """8. `GET editions/current/` bez logowania → 200 z etapami; bez danych uczestników."""
    edition = CurrentEditionFactory(year_label="XV (2026/2027)")
    elim = StageFactory(edition=edition, kind=StageKind.ELIM)
    StageFactory(
        edition=edition,
        kind=StageKind.DISTRICT,
        opens_at=timezone.now() + timedelta(days=60),
        deadline_at=timezone.now() + timedelta(days=74),
    )
    problem = ProblemFactory(stage=elim, number=1, title="Nierówność ze średnimi")
    entry = StageEntryFactory(stage=elim)

    resp = api.get(CURRENT_EDITION_URL)

    assert resp.status_code == 200, resp.data
    data = resp.json()
    assert data["year_label"] == "XV (2026/2027)"
    assert {stage["kind"] for stage in data["stages"]} == {"ELIM", "DISTRICT"}
    assert {"opens_at", "deadline_at", "results_published_at"} <= set(data["stages"][0])
    assert data["current_stage"]["kind"] == "ELIM"
    assert [item["title"] for item in data["problems"]] == [problem.title]

    body = resp.content.decode()
    assert entry.participant.public_code not in body
    assert entry.participant.user.email not in body
    assert entry.participant.school not in body
    assert "entries" not in data


@pytest.mark.django_db
def test_kryterium_8_edycja_archiwalna_nie_jest_biezaca(api):
    """8. Publiczny endpoint pokazuje wyłącznie edycję `is_current`; brak bieżącej → 404."""
    archived = EditionFactory(year_label="XIV (2025/2026)")
    StageFactory(edition=archived, kind=StageKind.ELIM)

    missing = api.get(CURRENT_EDITION_URL)
    assert missing.status_code == 404
    assert missing.json()["code"] == "NO_CURRENT_EDITION"

    current = CurrentEditionFactory(year_label="XV (2026/2027)")
    StageFactory(edition=current, kind=StageKind.ELIM)
    resp = api.get(CURRENT_EDITION_URL)
    assert resp.status_code == 200
    assert resp.json()["year_label"] == "XV (2026/2027)"


@pytest.mark.django_db
def test_kryterium_8_tresc_zadania_jest_ukryta_przed_otwarciem_etapu(api):
    """8. `statement_pdf` jest ujawniany dopiero po `opens_at` – wcześniej `None`."""
    edition = CurrentEditionFactory()
    now = timezone.now()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.ELIM,
        opens_at=now + timedelta(days=5),
        deadline_at=now + timedelta(days=19),
    )
    ProblemFactory(
        stage=stage,
        number=1,
        statement_pdf=SimpleUploadedFile("tresc.pdf", b"%PDF-1.4 demo", content_type="application/pdf"),
    )

    hidden = api.get(CURRENT_EDITION_URL).json()["problems"][0]
    assert hidden["statement_pdf"] is None
    assert hidden["number"] == 1

    stage.opens_at = now - timedelta(days=1)
    stage.save(update_fields=["opens_at"])
    visible = api.get(CURRENT_EDITION_URL).json()["problems"][0]
    assert visible["statement_pdf"] is not None


@pytest.mark.django_db
def test_me_entries_zwraca_wylacznie_wlasne_wpisy(api):
    """Filtr `for_user` w querysecie: uczestnik nie widzi wpisów innego uczestnika (brak IDOR)."""
    stage = StageFactory(kind=StageKind.ELIM)
    mine = StageEntryFactory(stage=stage)
    other = StageEntryFactory(stage=stage)
    api.force_authenticate(user=mine.participant.user)

    resp = api.get(MY_ENTRIES_URL)

    assert resp.status_code == 200, resp.data
    rows = resp.json()
    assert [row["id"] for row in rows] == [mine.id]
    assert other.participant.public_code not in resp.content.decode()


@pytest.mark.django_db
def test_me_entries_wymaga_roli_uczestnika(api):
    """Test negatywny uprawnień dla `me/entries/`: anonim → 401, recenzent → 403."""
    assert api.get(MY_ENTRIES_URL).status_code == 401

    api.force_authenticate(user=ActiveReviewerFactory().user)
    assert api.get(MY_ENTRIES_URL).status_code == 403
