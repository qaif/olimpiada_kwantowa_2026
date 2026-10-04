"""Bramka treści etapu, zgoda (z regułą opiekuna) i praca bez nadzoru (PROC-01 § 5–6)."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, URLPattern, URLResolver, get_resolver, resolve, reverse
from django.utils import timezone

from apps.accounts.consents import ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.proctoring import services
from apps.proctoring.middleware import GATED_VIEWS, ProctoringGateMiddleware
from apps.proctoring.models import AlternativeStatus, EventKind, OnUnavailable

from .conftest import make_student, ready_session

pytestmark = pytest.mark.django_db

HTML = {"HTTP_ACCEPT": "text/html"}

ROUTE_ARGS = {
    "competitions:problem-statement": [1],
    "web:problem-upload": [1, 1],
    "submissions:submission-create": [1, 1],
    "web:quiz-start": [1],
    "web:quiz-attempt": [1],
    # TR-01 (``apps.problem_translations``) – adresy pojawią się po scaleniu; do tego czasu pomijane.
    "web:student-translation": [1],
    "web:student-translation-file": [1],
}
OPTIONAL_ROUTES = {"web:student-translation", "web:student-translation-file"}

#: Nazwy adresów pasujące do „treści zadań”, które **świadomie** nie stoją za bramką – każda z powodem.
UNGATED_WITH_REASON = {
    "web:quiz-autosave": "autozapis odpowiedzi – nie może ginąć przez zerwany strumień",
    "web:quiz-result": "wynik po podejściu – nie odsłania zadań w trakcie",
    "web:problem-model-solution": "rozwiązanie wzorcowe – wyłącznie panel recenzenta",
    "web:participant-feedback": "informacja zwrotna po publikacji wyników",
}
#: Przestrzenie i prefiksy wyłącznie dla personelu (panel koordynatora, admin, API komisji) – oraz
#: dwie drogi, które treści zadań **uczniowi** nie dają: edytor tłumaczeń opiekuna drużyny (TR-01,
#: własne okno tłumaczeń i własne bramki) i przegląd tłumaczeń interfejsu (L10N-01 – napisy ekranów).
STAFF_PREFIXES = (
    "admin:",
    "wagtail",
    "simple_translation:",
    "web:coordinator-",
    "grading:",
    "integrations:",
    "web:delegation-translation",
    "web:translation",
)
CONTENT = re.compile(r"(problem|statement|quiz|translation|submission-create)")


def _names(resolver, ns=""):
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from _names(pattern, ns + (f"{pattern.namespace}:" if pattern.namespace else ""))
        elif isinstance(pattern, URLPattern) and pattern.name:
            yield ns + pattern.name


def test_every_url_serving_problem_content_is_gated_or_justified():
    """Przegląd **wszystkich** nazw adresów: nowa droga do treści zadań (np. tłumaczenia TR-01)
    bez wpisu w ``GATED_VIEWS`` albo uzasadnienia tutaj wywraca test, a nie bramkę po cichu."""
    unexplained = sorted(
        name
        for name in set(_names(get_resolver()))
        if CONTENT.search(name)
        and not name.startswith(STAFF_PREFIXES)
        and name not in GATED_VIEWS
        and name not in UNGATED_WITH_REASON
    )
    assert unexplained == []


def test_every_gated_route_name_exists():
    """Kontrakt z adresami innych aplikacji: zmiana nazwy bez poprawki bramki wywraca ten test."""
    assert set(ROUTE_ARGS) == set(GATED_VIEWS)
    for name, args in ROUTE_ARGS.items():
        try:
            url = reverse(name, args=args)
        except NoReverseMatch:
            assert name in OPTIONAL_ROUTES, name
            continue
        assert resolve(url).view_name == name


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


def test_flag_off_service_guard_costs_no_queries(competition, stage, student, as_competition):
    from apps.competitions.models import StageEntry

    entry = StageEntry.objects.get(participant=student)
    with as_competition(competition), CaptureQueriesContext(connection) as queries:
        services.assert_stage_access(stage, entry)
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


# --- H1: niezalogowani i osoby bez zgłoszenia ----------------------------------------------------


def test_anonymous_gets_no_statement_during_proctored_stage(
    proctoring_on, config, stage, client_for, competition
):
    problem = ProblemFactory(stage=stage)
    client = client_for(competition)
    url = f"/api/competitions/problems/{problem.pk}/statement/"
    html = client.get(url, **HTML)
    assert html.status_code == 302 and "login" in html["Location"]
    api = client.get(url, HTTP_ACCEPT="application/json")
    assert api.status_code == 403 and api.json()["code"] == "PROCTORING_STAGE_CLOSED"


def test_non_entrant_is_refused_but_staff_keeps_access(
    proctoring_on, config, stage, competition, coordinator, reviewer, logged
):
    from apps.accounts.models import CompetitionRole
    from apps.accounts.tests.factories import ParticipantFactory
    from apps.tenancy.tests.factories import grant_membership

    outsider = ParticipantFactory(competition=competition)
    grant_membership(outsider.user, competition, CompetitionRole.PARTICIPANT)
    assert services.gate_decision(outsider.user, stage, competition) == services.GATE_DENIED
    assert services.gate_decision(coordinator, stage, competition) is None
    assert services.gate_decision(reviewer, stage, competition) is None
    problem = ProblemFactory(stage=stage)
    response = logged(outsider.user).get(f"/api/competitions/problems/{problem.pk}/statement/", **HTML)
    assert response.status_code == 403


def test_disqualified_entrant_is_refused(proctoring_on, config, stage, student, competition):
    from apps.competitions.models import StageEntry, StageEntryStatus

    StageEntry.objects.filter(participant=student).update(status=StageEntryStatus.DISQUALIFIED)
    assert services.gate_decision(student.user, stage, competition) == services.GATE_DENIED


def test_gate_outside_window_does_not_interfere(proctoring_on, config, stage, student, competition):
    stage.opens_at = timezone.now() + timedelta(days=1)
    stage.deadline_at = stage.opens_at + timedelta(days=1)
    stage.save()
    assert services.gate_decision(student.user, stage, competition) is None
    assert services.gate_decision(None, stage, competition) is None


def test_stage_without_proctoring_is_untouched(proctoring_on, stage, student, competition):
    assert services.gate_decision(student.user, stage, competition) is None
    assert services.gate_decision(None, stage, competition) is None


# --- H2: token API przed sesją; druga linia obrony w serwisach -----------------------------------


def test_dummy_session_plus_students_own_token_does_not_bypass_the_gate(
    proctoring_on, config, stage, student, coordinator, logged
):
    """Sesja konta personelu (przepuszczona) + token ucznia w nagłówku: DRF przyjmie pracę od
    **właściciela tokenu**, więc bramka sprawdza właśnie jego – i odmawia."""
    from rest_framework.authtoken.models import Token

    ProblemFactory(stage=stage, number=1)
    token = Token.objects.create(user=student.user)
    response = logged(coordinator).post(
        f"/api/stages/{stage.pk}/problems/1/submissions/", HTTP_AUTHORIZATION=f"Token {token.key}"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PROCTORING_REQUIRED"


def test_api_upload_is_refused_without_proctoring(proctoring_on, config, stage, student, logged):
    ProblemFactory(stage=stage, number=1)
    response = logged(student.user).post(f"/api/stages/{stage.pk}/problems/1/submissions/")
    assert response.status_code == 403
    assert response.json()["code"] == "PROCTORING_REQUIRED"


def test_upload_is_refused_without_proctoring(proctoring_on, config, stage, student, logged):
    ProblemFactory(stage=stage, number=1)
    response = logged(student.user).post(f"/me/stages/{stage.pk}/problems/1/upload/", HTTP_HX_REQUEST="true")
    assert response.status_code == 403


def test_create_submission_service_refuses_without_proctoring(
    proctoring_on, config, stage, student, as_competition
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.submissions.services import create_submission

    ProblemFactory(stage=stage, number=1)
    upload = SimpleUploadedFile("praca.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf")
    with as_competition(stage.edition.competition), pytest.raises(DomainError) as error:
        create_submission(user=student.user, stage=stage, problem_number=1, upload=upload)
    assert error.value.machine_code == "PROCTORING_REQUIRED"


def test_quiz_start_service_refuses_without_proctoring(proctoring_on, config, stage, student, as_competition):
    from apps.competitions.models import StageEntry, StageFormat
    from apps.quiz.models import Quiz
    from apps.quiz.services import start_attempt

    stage.format = StageFormat.QUIZ
    stage.save(update_fields=["format"])
    quiz = Quiz.objects.create(stage=stage)
    entry = StageEntry.objects.get(participant=student)
    with as_competition(stage.edition.competition), pytest.raises(DomainError) as error:
        start_attempt(quiz=quiz, entry=entry)
    assert error.value.machine_code == "PROCTORING_REQUIRED"


# --- alternatywa, praca bez nadzoru (H3) -----------------------------------------------------------


def test_gate_open_after_approved_alternative(
    proctoring_on, config, stage, student, competition, coordinator
):
    session = services.session_for(stage, student)
    assert services.gate_decision(student.user, stage, competition) == services.GATE_CONSOLE
    services.request_alternative(session, "no_camera", "", user=student.user)
    assert services.gate_decision(student.user, stage, competition) == services.GATE_CONSOLE
    services.decide_alternative(session, approve=True, decision="telefon o 9:00", actor=coordinator)
    assert services.gate_decision(student.user, stage, competition) is None


def test_block_is_the_default(stage):
    from apps.proctoring.models import ProctoringConfig

    assert ProctoringConfig(stage=stage).on_unavailable == OnUnavailable.BLOCK


def allow(config):
    config.on_unavailable = OnUnavailable.ALLOW
    config.save()
    return config


def test_unproctored_when_server_not_configured(proctoring_on, config, stage, student, competition, settings):
    allow(config)
    settings.LIVEKIT_URL = ""
    session = ready_session(stage, student, started=False)
    services.continue_unproctored(session, config, user=student.user)
    session.refresh_from_db()
    assert session.unproctored_reason == services.UNPROCTORED_NOT_CONFIGURED
    assert services.gate_decision(student.user, stage, competition) is None
    detail = session.events.get(kind=EventKind.LIVEKIT_UNAVAILABLE).detail
    assert detail["server_reachable"] is False and detail["reason"] == "not_configured"


def test_unproctored_when_server_unreachable(proctoring_on, fake_livekit, config, stage, student):
    from apps.webinars.livekit import LiveKitUnavailable

    allow(config)
    session = ready_session(stage, student, started=False)
    fake_livekit.fail_with = LiveKitUnavailable("down")
    services.continue_unproctored(session, config, user=student.user)
    session.refresh_from_db()
    assert session.unproctored_reason == services.UNPROCTORED_SERVER_UNREACHABLE


def test_unproctored_refused_while_server_works_until_n_network_failures(
    proctoring_on, fake_livekit, config, stage, student
):
    allow(config)
    session = ready_session(stage, student, started=False)
    with pytest.raises(DomainError) as error:
        services.continue_unproctored(session, config, user=student.user)
    assert error.value.machine_code == "PROCTORING_SERVER_WORKS"
    # Odmowa kamery nie liczy się do awarii serwera – nawet wiele razy.
    for _attempt in range(5):
        services.client_event(session, EventKind.CONNECT_FAILED, reason="camera")
    with pytest.raises(DomainError):
        services.continue_unproctored(session, config, user=student.user)
    for _attempt in range(3):
        services.client_event(session, EventKind.CONNECT_FAILED, reason="connect")
    services.continue_unproctored(session, config, user=student.user)
    session.refresh_from_db()
    assert session.unproctored_reason == services.UNPROCTORED_CONNECT_FAILURES


def test_console_event_tells_whether_unproctored_is_allowed(
    proctoring_on, fake_livekit, config, stage, student, logged
):
    allow(config)
    ready_session(stage, student, started=False)
    client = logged(student.user)
    url = f"/me/proctoring/{stage.pk}/do/event/"
    first = client.post(url, {"kind": "connect_failed", "reason": "connect"}).json()
    assert first["unproctored_allowed"] is False
    client.post(url, {"kind": "connect_failed", "reason": "publish"})
    third = client.post(url, {"kind": "connect_failed", "reason": "token"}).json()
    assert third["unproctored_allowed"] is True


def test_unavailable_block_keeps_stage_closed(proctoring_on, config, stage, student, competition, settings):
    settings.LIVEKIT_URL = ""
    session = ready_session(stage, student, started=False)
    with pytest.raises(DomainError):
        services.continue_unproctored(session, config, user=student.user)
    assert services.gate_decision(student.user, stage, competition) == services.GATE_CONSOLE


def test_withdrawn_consent_closes_the_gate_before_start(
    proctoring_on, config, stage, student, competition, settings
):
    allow(config)
    settings.LIVEKIT_URL = ""
    session = ready_session(stage, student, started=False)
    services.continue_unproctored(session, config, user=student.user)
    services.withdraw_consent(session, user=student.user)
    assert services.gate_decision(student.user, stage, competition) == services.GATE_CONSOLE


# --- zgoda (M2) -------------------------------------------------------------------------------------


def test_consent_is_versioned_evidence_with_stage_terms(proctoring_on, config, stage, student):
    session = services.session_for(stage, student)
    consent = services.give_consent(session, user=student.user)
    assert consent.version == services.CONSENT_VERSION
    assert len(consent.text_sha256) == 64
    assert consent.terms == {"record": False, "microphone": False, "screen": False, "id_photo": "off"}
    assert consent.guardian_record is None
    assert session.events.filter(kind="consent_given").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record", True),
        ("require_microphone", True),
        ("require_screen_share", True),
        ("id_photo", "required"),
    ],
)
def test_changing_stage_terms_invalidates_consent(
    proctoring_on, config, stage, student, competition, field, value
):
    session = ready_session(stage, student, started=True)
    assert services.gate_decision(student.user, stage, competition) is None
    setattr(config, field, value)
    config.save()
    assert services.active_consent(session, config) is None
    assert services.gate_decision(student.user, stage, competition) == services.GATE_CONSOLE


def test_minor_needs_guardian_consent_confirmed_online_and_statement(
    proctoring_on, config, stage, competition
):
    minor = make_student(competition, stage, birth_year=timezone.now().year - 15)
    session = services.session_for(stage, minor)
    with pytest.raises(DomainError) as error:
        services.give_consent(session, user=minor.user, guardian_statement=True)
    assert error.value.machine_code == "PROCTORING_GUARDIAN_REQUIRED"
    # Oświadczenie dziecka „mój opiekun się zgodził” (bez adresu opiekuna) nie wystarcza.
    ConsentRecord.objects.create(participant=minor, kind=ConsentKind.GUARDIAN, source=ConsentSource.WEB)
    with pytest.raises(DomainError):
        services.give_consent(session, user=minor.user, guardian_statement=True)
    record = ConsentRecord.objects.create(
        participant=minor,
        kind=ConsentKind.GUARDIAN,
        source=ConsentSource.WEB,
        given_by_email="mama@example.com",
    )
    with pytest.raises(DomainError) as error:
        services.give_consent(session, user=minor.user)
    assert error.value.machine_code == "PROCTORING_GUARDIAN_STATEMENT"
    consent = services.give_consent(session, user=minor.user, guardian_statement=True)
    assert consent.guardian_record == record and consent.guardian_statement


def test_withdrawn_guardian_consent_voids_proctoring_consent(proctoring_on, config, stage, competition):
    minor = make_student(competition, stage, birth_year=timezone.now().year - 15)
    record = ConsentRecord.objects.create(
        participant=minor,
        kind=ConsentKind.GUARDIAN,
        source=ConsentSource.WEB,
        given_by_email="mama@example.com",
    )
    session = services.session_for(stage, minor)
    services.give_consent(session, user=minor.user, guardian_statement=True)
    session.check_passed_at = session.started_at = timezone.now()
    session.save()
    assert services.gate_decision(minor.user, stage, competition) is None
    ConsentRecord.objects.filter(pk=record.pk).update(withdrawn_at=timezone.now())
    assert services.active_consent(session, config) is None
    assert services.gate_decision(minor.user, stage, competition) == services.GATE_CONSOLE
    with pytest.raises(DomainError):
        services.student_token(session, config, user=minor.user)


def test_consent_view_requires_guardian_statement_for_minor(
    proctoring_on, config, stage, competition, logged
):
    minor = make_student(competition, stage, birth_year=timezone.now().year - 15)
    ConsentRecord.objects.create(
        participant=minor,
        kind=ConsentKind.GUARDIAN,
        source=ConsentSource.WEB,
        given_by_email="mama@example.com",
    )
    client = logged(minor.user)
    client.post(f"/me/proctoring/{stage.pk}/consent/", {"consent": "1"})
    assert not services.session_for(stage, minor).consents.exists()
    client.post(f"/me/proctoring/{stage.pk}/consent/", {"consent": "1", "guardian_statement": "1"})
    assert services.session_for(stage, minor).consents.exists()


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


@pytest.mark.parametrize("field", ["session", "user"])
def test_non_numeric_ids_are_404_not_500(proctoring_on, config, stage, coordinator, logged, field):
    action = {"session": "hold", "user": "assign"}[field]
    response = logged(coordinator).post(
        f"/coordinator/proctoring/{stage.pk}/", {"action": action, field: "abc"}
    )
    assert response.status_code == 404


def test_non_numeric_ack_is_404(proctoring_on, config, stage, student, logged):
    ready_session(stage, student)
    response = logged(student.user).post(f"/me/proctoring/{stage.pk}/do/ack/", {"id": "x1"})
    assert response.status_code == 404
