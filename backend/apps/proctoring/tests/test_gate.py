"""Bramka treści etapu, zgoda (z regułą opiekuna) i przełączniki (PROC-01 § 5–6)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from apps.accounts.consents import ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.proctoring import services
from apps.proctoring.middleware import GATED_VIEWS, ProctoringGateMiddleware
from apps.proctoring.models import AlternativeStatus, OnUnavailable

from .conftest import make_student, ready_session

pytestmark = pytest.mark.django_db

HTML = {"HTTP_ACCEPT": "text/html"}


ROUTE_ARGS = {
    "competitions:problem-statement": [1],
    "web:problem-upload": [1, 1],
    "submissions:submission-create": [1, 1],
    "web:quiz-start": [1],
    "web:quiz-attempt": [1],
}


def test_every_gated_route_name_exists():
    """Kontrakt z adresami innych aplikacji: zmiana nazwy bez poprawki bramki wywraca ten test."""
    assert set(ROUTE_ARGS) == set(GATED_VIEWS)
    for name, args in ROUTE_ARGS.items():
        assert resolve(reverse(name, args=args)).view_name == name


def test_flag_off_gate_costs_no_queries(competition, stage, student):
    problem = ProblemFactory(stage=stage)
    request = RequestFactory().get(f"/api/competitions/problems/{problem.pk}/statement/")
    request.competition = competition
    request.user = student.user
    request.resolver_match = resolve(request.path)
    middleware = ProctoringGateMiddleware(lambda r: None)
    with CaptureQueriesContext(connection) as queries:
        assert middleware.process_view(request, None, (), {"pk": problem.pk}) is None
    assert len(queries) == 0


def test_statement_redirects_to_console_until_proctoring_started(
    proctoring_on, config, stage, student, logged
):
    problem = ProblemFactory(stage=stage)
    client = logged(student.user)
    url = f"/api/competitions/problems/{problem.pk}/statement/"
    response = client.get(url, **HTML)
    assert response.status_code == 302
    assert response["Location"] == reverse("web:proctoring-console", args=[stage.pk])
    ready_session(stage, student, started=True)
    response = client.get(url, **HTML)
    assert response.status_code != 302 or "proctoring" not in response["Location"]


def test_upload_is_refused_without_proctoring(proctoring_on, config, stage, student, logged):
    ProblemFactory(stage=stage, number=1)
    response = logged(student.user).post(f"/me/stages/{stage.pk}/problems/1/upload/", HTTP_HX_REQUEST="true")
    assert response.status_code == 403


def test_api_upload_is_refused_without_proctoring(proctoring_on, config, stage, student, logged):
    ProblemFactory(stage=stage, number=1)
    response = logged(student.user).post(f"/api/stages/{stage.pk}/problems/1/submissions/")
    assert response.status_code == 403
    assert response.json()["code"] == "PROCTORING_REQUIRED"


def test_gate_lets_through_people_without_entry_and_stages_without_proctoring(
    proctoring_on, stage, coordinator, student, competition
):
    assert services.gate_blocks(coordinator, stage, competition) is False  # bez zgłoszenia
    assert services.gate_blocks(student.user, stage, competition) is False  # etap bez nadzoru


def test_gate_open_after_approved_alternative(
    proctoring_on, config, stage, student, competition, coordinator
):
    session = services.session_for(stage, student)
    assert services.gate_blocks(student.user, stage, competition)
    services.request_alternative(session, "no_camera", "", user=student.user)
    assert services.gate_blocks(student.user, stage, competition)
    services.decide_alternative(session, approve=True, decision="telefon o 9:00", actor=coordinator)
    assert services.gate_blocks(student.user, stage, competition) is False


def test_gate_outside_window_does_not_interfere(proctoring_on, config, stage, student, competition):
    stage.opens_at = timezone.now() + timedelta(days=1)
    stage.deadline_at = stage.opens_at + timedelta(days=1)
    stage.save()
    assert services.gate_blocks(student.user, stage, competition) is False


def test_unavailable_allow_opens_stage_with_a_flag(
    proctoring_on, config, stage, student, competition, settings
):
    settings.LIVEKIT_URL = ""  # serwer nieskonfigurowany – jak awaria
    session = ready_session(stage, student, started=False)
    services.continue_unproctored(session, config, user=student.user)
    session.refresh_from_db()
    assert session.unproctored_at is not None
    assert services.gate_blocks(student.user, stage, competition) is False
    detail = session.events.get(kind="livekit_unavailable").detail
    assert detail == {"server_reachable": False, "configured": False}


def test_unavailable_block_keeps_stage_closed(proctoring_on, config, stage, student, competition):
    config.on_unavailable = OnUnavailable.BLOCK
    config.save()
    session = ready_session(stage, student, started=False)
    with pytest.raises(DomainError):
        services.continue_unproctored(session, config, user=student.user)
    assert services.gate_blocks(student.user, stage, competition)


def test_withdrawn_consent_closes_the_gate_before_start(proctoring_on, config, stage, student, competition):
    session = ready_session(stage, student, started=False)
    services.continue_unproctored(session, config, user=student.user)
    services.withdraw_consent(session, user=student.user)
    assert services.gate_blocks(student.user, stage, competition)


# --- zgoda ---------------------------------------------------------------------------------------


def test_consent_is_versioned_evidence(proctoring_on, config, stage, student):
    session = services.session_for(stage, student)
    consent = services.give_consent(session, user=student.user)
    assert consent.version == services.CONSENT_VERSION
    assert len(consent.text_sha256) == 64
    assert consent.guardian_record is None
    assert session.events.filter(kind="consent_given").exists()


def test_minor_needs_guardian_consent_confirmed_online(proctoring_on, config, stage, competition):
    minor = make_student(competition, stage, birth_year=timezone.now().year - 15)
    session = services.session_for(stage, minor)
    with pytest.raises(DomainError) as error:
        services.give_consent(session, user=minor.user)
    assert error.value.machine_code == "PROCTORING_GUARDIAN_REQUIRED"
    # Oświadczenie dziecka „mój opiekun się zgodził” (bez adresu opiekuna) nie wystarcza.
    ConsentRecord.objects.create(participant=minor, kind=ConsentKind.GUARDIAN, source=ConsentSource.WEB)
    with pytest.raises(DomainError):
        services.give_consent(session, user=minor.user)
    record = ConsentRecord.objects.create(
        participant=minor,
        kind=ConsentKind.GUARDIAN,
        source=ConsentSource.WEB,
        given_by_email="mama@example.com",
    )
    consent = services.give_consent(session, user=minor.user)
    assert consent.guardian_record == record


def test_consent_view_shows_guardian_link_for_minor(proctoring_on, config, stage, competition, logged):
    minor = make_student(competition, stage, birth_year=timezone.now().year - 15)
    response = logged(minor.user).post(f"/me/proctoring/{stage.pk}/consent/", {"consent": "1"})
    assert response.status_code == 302
    assert not services.session_for(stage, minor).consents.exists()


def test_console_404_without_flag_or_entry(competition, config, stage, student, logged, coordinator):
    assert logged(student.user).get(f"/me/proctoring/{stage.pk}/").status_code == 404
    competition.feature_flags = {**(competition.feature_flags or {}), "proctoring": True}
    competition.save(update_fields=["feature_flags"])
    assert logged(student.user).get(f"/me/proctoring/{stage.pk}/").status_code == 200
    assert logged(coordinator).get(f"/me/proctoring/{stage.pk}/").status_code in (403, 404)


def test_alternative_request_and_decision_visible_in_console(
    proctoring_on, config, stage, student, logged, coordinator
):
    client = logged(student.user)
    client.post(
        f"/me/proctoring/{stage.pk}/alternative/", {"reason": "no_camera", "note": "laptop bez kamery"}
    )
    session = services.session_for(stage, student)
    assert session.alternative_status == AlternativeStatus.REQUESTED
    response = logged(coordinator).post(
        f"/coordinator/proctoring/{stage.pk}/",
        {"action": "alternative", "session": session.pk, "decision": "approve", "note": "telefon 9:00"},
    )
    assert response.status_code == 302
    session.refresh_from_db()
    assert session.alternative_approved
    data = client.get(f"/me/proctoring/{stage.pk}/messages/").json()
    assert data["ready"] is True and data["decision"] == "telefon 9:00"
