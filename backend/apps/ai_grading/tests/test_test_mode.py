"""Tryb testowy: praca testowa koordynatora, ocena każdym dostawcą z kluczem, widoczność tylko dla niego.

Dwie reguły są tu najważniejsze i każda ma test po obu stronach: bramka umowy powierzenia **nie**
dotyczy pracy testowej, ale **dalej** dotyczy prac uczestników; ocena testowa istnieje wyłącznie na
karcie zadania koordynatora – nie ma jej u recenzenta, uczestnika, w eksporcie ani w statystykach –
a jednocześnie kosztuje jak każda inna (liczniki zużycia i limit wydatków).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.ai_grading import sandbox, services, tasks
from apps.ai_grading.models import AiAssessment, AiAssessmentStatus, AiGradingSettings, AiTestWork
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.antivirus import VERDICT_INFECTED
from apps.submissions.models import AvStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import JPEG_BYTES, PDF_BYTES
from apps.tenancy.tests.factories import grant_membership

from .conftest import SENTINEL_SUMMARY, answer, enable_ai, make_submission, result, with_key

pytestmark = pytest.mark.django_db

#: Własny przykład koordynatora – inny plik niż ``PDF_BYTES`` prac uczestników.
SAMPLE_PDF = (
    b"%PDF-1.4\n% przyklad koordynatora\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TEST_SUMMARY = "SENTINEL-TEST-91ab: ocena pracy testowej"


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def model(monkeypatch):
    state = {"replies": [], "requests": []}

    def fake_call(api_key, request):
        state["requests"].append((api_key, request))
        reply = (
            state["replies"].pop(0)
            if state["replies"]
            else result(answer(summary=TEST_SUMMARY), model=request["model"])
        )
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(services, "call_model", fake_call)
    return state


@pytest.fixture
def no_dpa(competition, coordinator):
    """Konkurs z kluczami wszystkich dostawców i **bez** potwierdzonej umowy powierzenia."""
    enable_ai(competition)
    for name in ("anthropic", "openai", "google", "meta"):
        with_key(competition, coordinator, provider=name, dpa=False)
    return competition


def upload(name: str = "przyklad.pdf", content: bytes = SAMPLE_PDF, mime: str = "application/pdf"):
    return SimpleUploadedFile(name, content, content_type=mime)


def clean_work(problem, coordinator, django_capture_on_commit_callbacks, **kwargs) -> AiTestWork:
    with django_capture_on_commit_callbacks(execute=True):
        work = sandbox.upload_test_work(problem, upload(**kwargs), actor=coordinator, no_personal_data=True)
    work.refresh_from_db()
    return work


def run_all():
    for pk in AiAssessment.objects.filter(status=AiAssessmentStatus.PENDING).values_list("pk", flat=True):
        services.run_assessment(pk)


# --- wgranie -------------------------------------------------------------------------------------


def test_upload_is_validated_stored_and_scanned(
    no_dpa, problem, coordinator, django_capture_on_commit_callbacks, clamd
):
    work = clean_work(problem, coordinator, django_capture_on_commit_callbacks)

    assert work.av_status == AvStatus.CLEAN
    assert work.object_key.startswith(f"ai-test/{no_dpa.pk}/{problem.pk}/")
    assert clamd.scanned == [SAMPLE_PDF]
    assert get_submission_storage().open(work.object_key).read() == SAMPLE_PDF
    entry = AuditLog.objects.get(action="ai_grading.test_work_uploaded")
    assert entry.diff["no_personal_data"] is True


def test_png_is_accepted_and_a_fake_png_is_not(
    no_dpa, problem, coordinator, django_capture_on_commit_callbacks
):
    work = clean_work(
        problem,
        coordinator,
        django_capture_on_commit_callbacks,
        name="zrzut.png",
        content=PNG_BYTES,
        mime="image/png",
    )
    assert work.mime == "image/png"

    with pytest.raises(DomainError) as info:
        sandbox.upload_test_work(
            problem, upload("zrzut.png", SAMPLE_PDF, "image/png"), actor=coordinator, no_personal_data=True
        )
    assert info.value.machine_code == "INVALID_FILE_TYPE"


def test_content_validation_is_the_same_as_for_submissions(no_dpa, problem, coordinator):
    with pytest.raises(DomainError) as info:
        sandbox.upload_test_work(
            problem, upload("praca.pdf", b"nie pdf"), actor=coordinator, no_personal_data=True
        )

    assert info.value.machine_code == "INVALID_FILE_TYPE"
    assert not AiTestWork.objects.exists()


def test_the_declaration_is_required(no_dpa, problem, coordinator):
    with pytest.raises(DomainError) as info:
        sandbox.upload_test_work(problem, upload(), actor=coordinator, no_personal_data=False)

    assert info.value.machine_code == "AI_TEST_DECLARATION"


def test_a_participants_file_cannot_become_a_test_work(no_dpa, problem, coordinator):
    submission = make_submission(problem)
    submission.files.update(sha256=hashlib.sha256(PDF_BYTES).hexdigest())

    with pytest.raises(DomainError) as info:
        sandbox.upload_test_work(problem, upload(content=PDF_BYTES), actor=coordinator, no_personal_data=True)

    assert info.value.machine_code == "AI_TEST_IS_SUBMISSION"


def test_infected_test_work_is_removed_from_storage(
    no_dpa, problem, coordinator, django_capture_on_commit_callbacks, clamd
):
    clamd.verdict = (VERDICT_INFECTED, "Eicar-Test-Signature")
    with django_capture_on_commit_callbacks(execute=True):
        work = sandbox.upload_test_work(problem, upload(), actor=coordinator, no_personal_data=True)
        key = work.object_key
    work.refresh_from_db()

    assert work.av_status == AvStatus.INFECTED
    assert work.object_key == ""
    assert not get_submission_storage().exists(key)
    with pytest.raises(DomainError) as info:
        sandbox.request_test_assessment(work, provider="openai", model="gpt-6-sol", actor=coordinator)
    assert info.value.machine_code == "AI_TEST_NOT_CLEAN"


# --- bramka umowy powierzenia: tak dla prac uczestników, nie dla pracy testowej ---------------------


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("anthropic", "claude-opus-5"),
        ("openai", "gpt-6-astra"),
        ("google", "gemini-3.8-flash"),
        ("meta", "muse-spark-1.3"),
    ],
)
def test_every_provider_with_a_key_can_assess_a_test_work_without_dpa(
    no_dpa, problem, coordinator, model, django_capture_on_commit_callbacks, provider, model_id
):
    work = clean_work(problem, coordinator, django_capture_on_commit_callbacks)

    sandbox.request_test_assessment(work, provider=provider, model=model_id, actor=coordinator)
    run_all()

    assessment = AiAssessment.objects.get(test_work=work)
    assert assessment.status == AiAssessmentStatus.DONE
    assert (assessment.provider, assessment.requested_model) == (provider, model_id)
    [(_, request)] = model["requests"]
    assert request.provider == provider


def test_the_same_providers_still_get_no_participant_work_without_dpa(no_dpa, problem, coordinator):
    make_submission(problem)

    for provider in ("anthropic", "openai", "google", "meta"):
        with pytest.raises(DomainError) as info:
            services.request_generation(problem, regenerate=False, actor=coordinator, provider=provider)
        assert info.value.machine_code == "AI_DPA_MISSING"
    assert not AiAssessment.objects.exists()


def test_a_provider_without_a_key_cannot_assess_a_test_work(
    competition, problem, coordinator, django_capture_on_commit_callbacks
):
    enable_ai(competition)
    with_key(competition, coordinator, dpa=False)
    work = clean_work(problem, coordinator, django_capture_on_commit_callbacks)

    with pytest.raises(DomainError) as info:
        sandbox.request_test_assessment(work, provider="google", model="gemini-3.8-flash", actor=coordinator)

    assert info.value.machine_code == "AI_KEY_MISSING"


# --- widoczność: wyłącznie koordynator ---------------------------------------------------------------


@pytest.fixture
def world(no_dpa, stage, problem, coordinator, model, django_capture_on_commit_callbacks):
    """Praca uczestnika z oceną Anthropic (umowa potwierdzona) i praca testowa z oceną OpenAI."""
    services.set_dpa_confirmation(no_dpa, "anthropic", True, actor=coordinator)
    submission = make_submission(problem)
    FinalGradeFactory(submission=submission, score=5)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    model["replies"] = [result()]
    run_all()
    work = clean_work(problem, coordinator, django_capture_on_commit_callbacks)
    model["replies"] = [result(answer(proposed_points=0, summary=TEST_SUMMARY), model="gpt-6-sol")]
    sandbox.request_test_assessment(work, provider="openai", model="gpt-6-sol", actor=coordinator)
    run_all()
    return {"submission": submission, "work": work}


def test_test_assessments_stay_out_of_statistics_and_counts(world, problem):
    overview = services.problem_overview(problem)

    assert overview["counts"]["DONE"] == 1
    assert overview["agreement"]["count"] == 1 and overview["agreement"]["mean_abs_diff"] == Decimal("0.00")
    assert overview["agreement_by_model"] == []
    [item] = overview["test_works"]
    assert [assessment.provider for assessment in item["assessments"]] == ["openai"]


def test_test_assessments_count_toward_usage_and_the_spending_limit(world, no_dpa, coordinator):
    row = services.settings_for(no_dpa)
    assert row.total_calls == 2  # praca uczestnika + praca testowa
    AiGradingSettings.objects.filter(pk=row.pk).update(spending_limit_usd=row.total_cost_usd)

    with pytest.raises(DomainError) as info:
        sandbox.request_test_assessment(
            world["work"], provider="google", model="gemini-3.8-flash", actor=coordinator
        )
    assert info.value.machine_code == "AI_BUDGET_REACHED"


def test_reviewer_never_sees_a_test_assessment(world):
    reviewer = ActiveReviewerFactory()
    review = ReviewFactory(submission=world["submission"], reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    client = Client()
    client.force_login(reviewer.user)

    content = client.get(f"/review/{review.pk}/").content.decode()

    assert SENTINEL_SUMMARY in content  # zwykła ocena jest
    assert TEST_SUMMARY not in content
    assert "gpt-6-sol" not in content


def test_participant_and_export_never_see_a_test_assessment(world):
    from apps.ai_grading.models import AiStageVisibility
    from apps.results.models import ResultsPublication

    participant = world["submission"].entry.participant
    stage = world["submission"].entry.stage
    AiStageVisibility.objects.create(stage=stage, show_to_participants=True)
    ResultsPublication.objects.create(stage=stage)

    dumped = json.dumps(
        [services.participant_ai_feedback(participant, stage), services.export_section(participant)],
        ensure_ascii=False,
        default=str,
    )

    assert SENTINEL_SUMMARY in dumped
    assert TEST_SUMMARY not in dumped and "OpenAI" not in dumped


def test_register_does_not_list_a_provider_used_only_for_tests(world, no_dpa):
    from apps.accounts.processing_register import ai_grading_activity

    recipients = " ".join(ai_grading_activity(no_dpa).recipients)

    assert "Anthropic" in recipients and "OpenAI" not in recipients


def test_problem_card_shows_test_assessments_labelled_test(world, problem, coordinator):
    client = Client()
    client.force_login(coordinator)

    content = client.get(f"/coordinator/problems/{problem.pk}/").content.decode()

    assert TEST_SUMMARY in content
    assert 'id="ocena-ai-test"' in content and "TEST – widzi tylko koordynator" in content


def test_test_assessments_and_works_can_be_deleted(world, coordinator):
    assessment = AiAssessment.objects.get(test_work=world["work"])

    sandbox.delete_test_assessment(assessment, actor=coordinator)
    assert not AiAssessment.objects.filter(test_work=world["work"]).exists()

    key = world["work"].object_key
    sandbox.delete_test_work(world["work"], actor=coordinator)
    assert not AiTestWork.objects.exists()
    assert AiAssessment.objects.filter(submission=world["submission"]).count() == 1
    with pytest.raises(DomainError):
        sandbox.delete_test_assessment(AiAssessment.objects.get(), actor=coordinator)  # nie testowa
    assert key


# --- ekran -----------------------------------------------------------------------------------------


def test_test_mode_through_the_problem_card(
    no_dpa, problem, coordinator, model, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(tasks.run_ai_assessment, "delay", lambda *args, **kwargs: None)
    client = Client()
    client.force_login(coordinator)
    url = f"/coordinator/problems/{problem.pk}/ai/test/"

    with django_capture_on_commit_callbacks(execute=True):
        client.post(url, {"action": "upload", "file": upload(), "no_personal_data": "on", "label": "wzór A"})
    work = AiTestWork.objects.get()
    assert work.label == "wzór A" and work.av_status == AvStatus.CLEAN

    card = client.get(f"/coordinator/problems/{problem.pk}/").content.decode()
    assert "Wygeneruj ocenę testową" in card
    assert 'value="meta:muse-spark-1.3"' in card  # każdy dostawca z kluczem, mimo braku umów

    client.post(url, {"action": "generate", "work": work.pk, "target": "google:gemini-3.8-flash"})
    assessment = AiAssessment.objects.get()
    assert (assessment.test_work_id, assessment.provider) == (work.pk, "google")

    run_all()
    client.post(url, {"action": "delete_assessment", "assessment": assessment.pk})
    client.post(url, {"action": "delete_work", "work": work.pk})
    assert not AiTestWork.objects.exists()


def test_upload_without_the_declaration_is_refused_on_screen(no_dpa, problem, coordinator):
    client = Client()
    client.force_login(coordinator)

    response = client.post(
        f"/coordinator/problems/{problem.pk}/ai/test/", {"action": "upload", "file": upload()}, follow=True
    )

    assert "Potwierdź, że plik nie zawiera danych osobowych" in response.content.decode()
    assert not AiTestWork.objects.exists()


def test_test_endpoint_is_for_coordinators_only(no_dpa, problem):
    participant = ParticipantFactory()
    grant_membership(participant.user, no_dpa, CompetitionRole.PARTICIPANT)
    reviewer = ActiveReviewerFactory()
    client = Client()
    for user in (participant.user, reviewer.user):
        client.force_login(user)
        response = client.post(
            f"/coordinator/problems/{problem.pk}/ai/test/",
            {"action": "upload", "file": upload(), "no_personal_data": "on"},
        )
        assert response.status_code == 403
    assert not AiTestWork.objects.exists()


def test_test_work_of_another_problem_is_404(
    no_dpa, problem, stage, coordinator, django_capture_on_commit_callbacks
):
    from apps.competitions.tests.factories import ProblemFactory

    work = clean_work(problem, coordinator, django_capture_on_commit_callbacks)
    other = ProblemFactory(stage=stage, number=8)
    client = Client()
    client.force_login(coordinator)

    response = client.post(
        f"/coordinator/problems/{other.pk}/ai/test/", {"action": "delete_work", "work": work.pk}
    )

    assert response.status_code == 404
    assert AiTestWork.objects.exists()


def test_image_test_work_goes_to_the_provider_as_an_image(
    no_dpa, problem, coordinator, model, django_capture_on_commit_callbacks
):
    work = clean_work(
        problem,
        coordinator,
        django_capture_on_commit_callbacks,
        name="zdjecie.jpg",
        content=JPEG_BYTES,
        mime="image/jpeg",
    )

    sandbox.request_test_assessment(work, provider="openai", model="gpt-6-sol", actor=coordinator)
    run_all()

    [(_, request)] = model["requests"]
    assert request["input"][0]["content"][4]["type"] == "input_image"
