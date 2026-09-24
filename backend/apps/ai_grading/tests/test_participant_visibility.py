"""Uczestnik a ocena AI: nic nie przecieka, dopóki koordynator jej nie pokaże.

Ocena AI jest sugestią dla komitetu. Uczestnik widzi ją **wyłącznie** wtedy, gdy koordynator
włączy „Pokaż uczestnikom ocenę AI” dla etapu, i dopiero po ogłoszeniu wyników – obok oficjalnej
oceny, z podpisem „sugestia AI”. Przy wyłączonym przełączniku (stan domyślny) ten plik pilnuje,
żeby treść sugestii nie pojawiła się na **żadnym** ekranie uczestnika: panel, informacja zwrotna,
tabela wyników, archiwum, dyplomy, reklamacje, API uczestnika i eksport danych.

Wyjątek jest jeden i świadomy: eksport danych konta (art. 15/20 RODO) zawiera **fakt**, że praca
została przekazana do oceny AI (komu i kiedy) – bo odbiorca danych jest informacją, do której
osoba ma prawo z art. 15 ust. 1 lit. c. Treści sugestii ani punktów tam nie ma.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.ai_grading import services
from apps.ai_grading.models import AiAssessment
from apps.competitions.tests.factories import StageEntryFactory
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.services import publish_results
from apps.submissions.models import SubmissionStatus
from apps.tenancy.tests.factories import grant_membership
from apps.web.tests.conftest import close_stage_timeline

from .conftest import SENTINEL_SUMMARY, enable_ai, make_submission, result, with_key

pytestmark = pytest.mark.django_db

#: Napisy, po których rozpoznalibyśmy ocenę AI na ekranie uczestnika.
AI_MARKERS = (SENTINEL_SUMMARY, "Ocena AI", "sugestia AI", "ocen AI", "ocena AI")


@pytest.fixture
def world(competition, stage, problem, monkeypatch):
    """Uczestnik z oceną końcową i gotową oceną AI w etapie z ogłoszonymi wynikami."""
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    participant = ParticipantFactory(
        user=UserFactory(email="uczestnik-ai@example.test", groups=["participant"])
    )
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    entry = StageEntryFactory(participant=participant, stage=stage)
    submission = make_submission(problem, entry=entry, status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=submission, score=5)

    enable_ai(competition)
    with_key(competition, coordinator)
    monkeypatch.setattr(services, "call_model", lambda key, request: result())
    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.run_assessment(AiAssessment.objects.get(submission=submission).pk)

    close_stage_timeline(stage)
    stage.refresh_from_db()
    publish_results(stage, coordinator, "CODE")
    return {"participant": participant, "coordinator": coordinator, "stage": stage, "submission": submission}


def participant_pages(stage) -> list[str]:
    return [
        "/me/",
        f"/me/stages/{stage.pk}/feedback/",
        f"/results/{stage.pk}/",
        "/me/archive/",
        "/me/certificates/",
        "/api/me/results/",
        "/api/me/appeals/",
        "/api/me/submissions/",
        f"/api/public/results/{stage.pk}/",
    ]


def export(client) -> dict:
    response = client.get("/account/export/")
    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as package:
        return json.loads(package.read("dane.json").decode("utf-8"))


def test_nothing_ai_related_reaches_the_participant_while_the_toggle_is_off(world):
    client = Client()
    client.force_login(world["participant"].user)

    for url in participant_pages(world["stage"]):
        response = client.get(url)
        assert response.status_code in (200, 404), url
        content = response.content.decode()
        for marker in AI_MARKERS:
            assert marker not in content, (url, marker)


def test_the_export_lists_the_fact_of_processing_but_not_the_suggestion(world):
    client = Client()
    client.force_login(world["participant"].user)

    data = export(client)

    dumped = json.dumps(data, ensure_ascii=False)
    assert SENTINEL_SUMMARY not in dumped
    [item] = data["oceny_ai"]
    assert item["tresc"] is None
    assert "Anthropic" in item["przekazano_do"]
    assert item["zadanie_numer"] == world["submission"].problem.number
    assert item["przekazano"]


def test_with_the_toggle_on_and_results_published_the_participant_sees_the_labelled_suggestion(world):
    services.set_stage_visibility(world["stage"], True, actor=world["coordinator"])
    client = Client()
    client.force_login(world["participant"].user)

    content = client.get(f"/me/stages/{world['stage'].pk}/feedback/").content.decode()

    assert "Ocena AI (sugestia, niewiążąca)" in content
    assert SENTINEL_SUMMARY in content
    assert "sugestia AI: 5 z 6 pkt" in content
    # Oficjalna ocena nadal jest oficjalną oceną – sugestia stoi obok, a nie zamiast.
    assert "5 pkt" in content
    assert export(client)["oceny_ai"][0]["tresc"]["podsumowanie"] == SENTINEL_SUMMARY


def test_the_toggle_does_not_open_anything_before_results_are_published(
    competition, stage, problem, monkeypatch
):
    coordinator = CoordinatorFactory()
    participant = ParticipantFactory()
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    entry = StageEntryFactory(participant=participant, stage=stage)
    submission = make_submission(problem, entry=entry)
    enable_ai(competition)
    with_key(competition, coordinator)
    monkeypatch.setattr(services, "call_model", lambda key, request: result())
    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.run_assessment(AiAssessment.objects.get(submission=submission).pk)
    services.set_stage_visibility(stage, True, actor=coordinator)

    assert services.participant_ai_feedback(participant, stage) == []
    client = Client()
    client.force_login(participant.user)
    assert SENTINEL_SUMMARY not in client.get("/me/").content.decode()
    assert SENTINEL_SUMMARY not in json.dumps(export(client), ensure_ascii=False)


def test_the_toggle_is_ignored_when_the_competition_flag_is_off(world, competition):
    services.set_stage_visibility(world["stage"], True, actor=world["coordinator"])
    competition.feature_flags = {**competition.feature_flags, "ai_grading": False}
    competition.save(update_fields=["feature_flags"])
    client = Client()
    client.force_login(world["participant"].user)

    content = client.get(f"/me/stages/{world['stage'].pk}/feedback/").content.decode()

    assert SENTINEL_SUMMARY not in content
    assert export(client)["oceny_ai"][0]["tresc"] is None


def test_other_participants_never_see_someone_elses_suggestion(world):
    services.set_stage_visibility(world["stage"], True, actor=world["coordinator"])
    stranger = ParticipantFactory()
    StageEntryFactory(participant=stranger, stage=world["stage"])

    assert services.participant_ai_feedback(stranger, world["stage"]) == []
