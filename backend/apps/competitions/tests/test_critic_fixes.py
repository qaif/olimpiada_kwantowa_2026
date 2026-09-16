"""Testy do findings Critica z T-03 (admin, statement_pdf, allowed_values, bieżąca edycja, /me/entries/)."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import (
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageFormat,
    StageKind,
)
from apps.competitions.services import ensure_stage_defaults, register_for_stage
from apps.competitions.video import VideoProvider
from apps.core.api import DomainError

from .factories import CurrentEditionFactory, EditionFactory, ProblemFactory, StageEntryFactory, StageFactory


@pytest.mark.django_db
def test_allowed_values_ignoruje_bool():
    scale = ScoringScale(values=[{"value": 0}, {"value": True}, {"value": 2}], max_value=2)
    assert scale.allowed_values() == {0, 2}


@pytest.mark.django_db
def test_register_wymaga_biezacej_edycji():
    now = timezone.now()
    stage = StageFactory(
        edition=EditionFactory(is_current=False),
        kind=StageKind.ELIM,
        opens_at=now - timedelta(days=1),
        deadline_at=now + timedelta(days=1),
    )
    with pytest.raises(DomainError) as exc:
        register_for_stage(ParticipantFactory(), stage)
    assert exc.value.machine_code == "REGISTRATION_CLOSED"


@pytest.mark.django_db
def test_ensure_stage_defaults_dopina_skale_i_prog_i_jest_idempotentne():
    stage = StageFactory()
    ScoringScale.objects.filter(stage=stage).delete()
    QualificationRule.objects.filter(stage=stage).delete()
    ensure_stage_defaults(stage)
    ensure_stage_defaults(stage)
    stage.refresh_from_db()
    assert ScoringScale.objects.get(stage=stage).allowed_values() == {0, 2, 5, 6}
    assert QualificationRule.objects.get(stage=stage).mode == "MIN_POINTS"
    assert ScoringScale.objects.filter(stage=stage).count() == 1


@pytest.mark.django_db
def test_admin_tworzy_etap_ze_skala_i_progiem(client):
    coordinator = CoordinatorFactory(is_staff=True, is_superuser=True)
    client.force_login(coordinator)
    edition = CurrentEditionFactory()
    now = timezone.now()

    def d(days):
        return (now + timedelta(days=days)).strftime("%Y-%m-%d")

    t = now.strftime("%H:%M:%S")
    data = {
        "edition": edition.pk,
        "kind": StageKind.ELIM,
        "name": "",
        "format": StageFormat.SUBMISSIONS,
        "opens_at_0": d(0),
        "opens_at_1": t,
        "deadline_at_0": d(1),
        "deadline_at_1": t,
        "grace_seconds": 0,
        "review_deadline_at_0": d(2),
        "review_deadline_at_1": t,
        # Dni na jedną recenzję – pole etapu wprowadzone razem z terminami pojedynczych recenzji.
        "review_deadline_days": 14,
        "appeal_window_opens_at_0": d(3),
        "appeal_window_opens_at_1": t,
        "appeal_window_closes_at_0": d(4),
        "appeal_window_closes_at_1": t,
        # Pokój wideo rozmowy kwalifikacyjnej. W adminie pole jest wymagane jak każde inne
        # z zamkniętą listą – panel koordynatora przyjmuje je jako opcjonalne (patrz StageForm).
        "video_provider": VideoProvider.NONE,
        "video_base_url": "",
    }
    # puste formsety inline – prefiksy odczytane z formularza GET, żeby nie zgadywać nazw
    get = client.get("/admin/competitions/stage/add/")
    assert get.status_code == 200
    for formset in get.context["inline_admin_formsets"]:
        prefix = formset.formset.prefix
        data[f"{prefix}-TOTAL_FORMS"] = 0
        data[f"{prefix}-INITIAL_FORMS"] = 0
    resp = client.post("/admin/competitions/stage/add/", data)
    assert resp.status_code == 302, resp.content[:3000]
    stage = Stage.objects.get(edition=edition, kind=StageKind.ELIM)
    assert ScoringScale.objects.filter(stage=stage).exists()
    assert QualificationRule.objects.filter(stage=stage).exists()


@pytest.mark.django_db
def test_tresc_zadania_nieosiagalna_przed_otwarciem_i_osiagalna_po(client):
    now = timezone.now()
    stage = StageFactory(
        edition=CurrentEditionFactory(),
        opens_at=now + timedelta(days=1),
        deadline_at=now + timedelta(days=2),
    )
    problem = ProblemFactory(
        stage=stage,
        statement_pdf=SimpleUploadedFile("tresc.pdf", b"%PDF-1.4 demo", content_type="application/pdf"),
    )
    url = f"/api/competitions/problems/{problem.pk}/statement/"
    assert client.get(url).status_code == 404
    Stage.objects.filter(pk=stage.pk).update(opens_at=now - timedelta(hours=1))
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert b"".join(resp.streaming_content).startswith(b"%PDF-")


@pytest.mark.django_db
def test_me_entries_koordynatora_z_profilem_uczestnika_nie_zwraca_cudzych_wpisow():
    coordinator = CoordinatorFactory()
    coordinator.groups.add(Group.objects.get(name="participant"))
    own = ParticipantFactory(user=coordinator)
    stage = StageFactory(edition=CurrentEditionFactory())
    StageEntryFactory(participant=own, stage=stage)
    StageEntryFactory(participant=ParticipantFactory(), stage=stage)
    client = APIClient()
    client.force_authenticate(coordinator)
    resp = client.get("/api/competitions/me/entries/")
    assert resp.status_code == 200
    assert [e["public_code"] for e in resp.json()] == [own.public_code]
    assert StageEntry.objects.count() == 2
