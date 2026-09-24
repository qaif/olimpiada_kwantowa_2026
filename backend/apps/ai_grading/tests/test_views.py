"""Ekrany oceny AI: bramki (rola, flaga, konkurs), klucz tylko do zapisu, zlecenie, panele.

Kolejność bramek jest ta sama co przy moderacji forum: rola przed flagą. Uczestnik i recenzent
dostają 403 niezależnie od przełącznika, koordynator konkursu z wyłączoną oceną AI – 404, a
identyfikator z sąsiedniej olimpiady – 404 także przy włączonej.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.ai_grading import services, tasks
from apps.ai_grading.models import AiAssessment, AiAssessmentStatus, AiStageVisibility
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import ReviewFactory
from apps.tenancy.tests.factories import grant_membership

from .conftest import FAKE_KEY, SENTINEL_SUMMARY, enable_ai, make_submission, result, with_key

pytestmark = pytest.mark.django_db

SETTINGS_URL = "/coordinator/ai-grading/"
MENU_LABEL = "Ocena AI"


def coordinator_of(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def generate_url(problem) -> str:
    return f"/coordinator/problems/{problem.pk}/ai/generate/"


def progress_url(problem) -> str:
    return f"/coordinator/problems/{problem.pk}/ai/"


@pytest.fixture
def web():
    return Client()


@pytest.fixture
def coordinator(competition):
    return coordinator_of(competition)


@pytest.fixture
def dispatched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tasks.run_ai_assessment, "delay", lambda *args, **kwargs: calls.append(args))
    return calls


@pytest.fixture
def done(competition, problem, coordinator, monkeypatch):
    """Praca z gotową oceną AI (model podmieniony)."""
    enable_ai(competition)
    with_key(competition, coordinator)
    monkeypatch.setattr(services, "call_model", lambda key, request: result())
    submission = make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.run_assessment(AiAssessment.objects.get(submission=submission).pk)
    return submission


# --- bramki ---------------------------------------------------------------------------------------


def test_every_screen_is_404_without_the_flag(web, coordinator, problem):
    web.force_login(coordinator)

    assert web.get(SETTINGS_URL).status_code == 404
    assert web.get(progress_url(problem)).status_code == 404
    assert web.post(generate_url(problem), {"action": "preview"}).status_code == 404


@pytest.mark.parametrize("flag_on", [False, True])
def test_participant_and_reviewer_are_refused_whatever_the_flag_says(web, competition, problem, flag_on):
    if flag_on:
        enable_ai(competition)
    participant = ParticipantFactory()
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    reviewer = ActiveReviewerFactory()

    for user in (participant.user, reviewer.user):
        web.force_login(user)
        assert web.get(SETTINGS_URL).status_code == 403
        assert web.post(generate_url(problem), {"action": "confirm"}).status_code == 403


def test_problem_of_another_competition_is_404(web, competition, coordinator, other_competition):
    enable_ai(competition)
    with_key(competition, coordinator)
    foreign = ProblemFactory(stage=StageFactory(competition=other_competition), competition=other_competition)
    web.force_login(coordinator)

    assert web.get(progress_url(foreign)).status_code == 404
    assert web.post(generate_url(foreign), {"action": "confirm"}).status_code == 404


def test_stage_of_another_competition_cannot_be_toggled(web, competition, coordinator, other_competition):
    enable_ai(competition)
    foreign = StageFactory(competition=other_competition)
    web.force_login(coordinator)

    response = web.post(SETTINGS_URL, {"action": "visibility", "stage": foreign.pk, "show": "1"})

    assert response.status_code == 404
    assert not AiStageVisibility.objects.exists()


def test_menu_item_follows_the_flag(web, competition, coordinator):
    web.force_login(coordinator)
    assert MENU_LABEL not in web.get("/coordinator/").content.decode()

    enable_ai(competition)
    assert MENU_LABEL in web.get("/coordinator/").content.decode()


# --- klucz ----------------------------------------------------------------------------------------


def test_key_is_write_only_on_the_screen(web, competition, coordinator):
    enable_ai(competition)
    web.force_login(coordinator)

    response = web.post(SETTINGS_URL, {"action": "set_key", "api_key": FAKE_KEY}, follow=True)
    content = response.content.decode()

    assert response.status_code == 200
    assert FAKE_KEY not in content
    assert "kończy się na …WXYZ" in content
    assert services.account_for(competition, "anthropic").has_key


def test_invalid_key_is_refused_and_not_echoed_back(web, competition, coordinator):
    enable_ai(competition)
    web.force_login(coordinator)

    response = web.post(SETTINGS_URL, {"action": "set_key", "api_key": "moje-tajne-haslo-123"}, follow=True)
    content = response.content.decode()

    assert "moje-tajne-haslo-123" not in content
    assert not services.account_for(competition, "anthropic").has_key


def test_key_can_be_removed_and_checked(web, competition, coordinator, monkeypatch):
    enable_ai(competition)
    with_key(competition, coordinator)
    monkeypatch.setattr(
        services,
        "check_key",
        lambda key, model, provider="anthropic": (False, "Anthropic odrzucił klucz API."),
    )
    web.force_login(coordinator)

    checked = web.post(SETTINGS_URL, {"action": "check_key"}, follow=True).content.decode()
    assert "Anthropic odrzucił klucz API." in checked

    web.post(SETTINGS_URL, {"action": "remove_key"})
    assert not services.account_for(competition, "anthropic").has_key


def test_model_and_limit_are_saved(web, competition, coordinator):
    enable_ai(competition)
    web.force_login(coordinator)

    web.post(SETTINGS_URL, {"action": "options", "model": "claude-sonnet-5", "spending_limit_usd": "25.50"})

    row = services.settings_for(competition)
    assert row.model == "claude-sonnet-5"
    assert str(row.spending_limit_usd) == "25.50"


# --- zlecenie -------------------------------------------------------------------------------------


def test_generate_shows_count_and_estimate_before_spending_anything(web, competition, coordinator, problem):
    enable_ai(competition)
    with_key(competition, coordinator)
    make_submission(problem)
    make_submission(problem)
    web.force_login(coordinator)

    response = web.post(generate_url(problem), {"action": "preview"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "Prace do oceny" in content and "<strong>2</strong>" in content
    assert "USD" in content
    assert not AiAssessment.objects.exists()


def test_confirm_queues_and_redirects_to_the_problem(
    web, competition, coordinator, problem, dispatched, django_capture_on_commit_callbacks
):
    enable_ai(competition)
    with_key(competition, coordinator)
    make_submission(problem)
    web.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(generate_url(problem), {"action": "confirm"})

    assert response.status_code == 302
    assert response["Location"].endswith(f"/coordinator/problems/{problem.pk}/#ocena-ai")
    assert AiAssessment.objects.get().status == AiAssessmentStatus.PENDING
    assert len(dispatched) == 1


def test_problem_card_shows_the_ai_section_only_with_the_flag(web, competition, coordinator, done, problem):
    web.force_login(coordinator)
    content = web.get(f"/coordinator/problems/{problem.pk}/").content.decode()
    assert 'id="ocena-ai"' in content
    assert SENTINEL_SUMMARY in content

    competition.feature_flags = {**competition.feature_flags, "ai_grading": False}
    competition.save(update_fields=["feature_flags"])
    content = web.get(f"/coordinator/problems/{problem.pk}/").content.decode()
    assert 'id="ocena-ai"' not in content
    assert SENTINEL_SUMMARY not in content


# --- panel recenzenta -----------------------------------------------------------------------------


def test_reviewer_sees_the_suggestion_for_an_assigned_work_without_identity(web, done):
    reviewer = ActiveReviewerFactory()
    review = ReviewFactory(submission=done, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web.force_login(reviewer.user)

    content = web.get(f"/review/{review.pk}/").content.decode()

    assert "Ocena AI – Anthropic claude-opus-5 (sugestia, niewiążąca)" in content
    assert SENTINEL_SUMMARY in content
    assert 'data-ai-prefill="5"' in content
    participant = done.entry.participant
    for personal in (participant.user.last_name, participant.user.email, participant.school):
        assert personal not in content


def test_suggestion_never_fills_the_score_by_itself(web, done):
    reviewer = ActiveReviewerFactory()
    review = ReviewFactory(submission=done, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    web.force_login(reviewer.user)

    content = web.get(f"/review/{review.pk}/").content.decode()

    radios = re.findall(r'<input type="radio" name="score"[^>]*>', content)
    assert len(radios) == 4
    assert not any("checked" in radio for radio in radios)
    assert review.score is None


def test_reviewer_without_the_assignment_cannot_reach_the_suggestion(web, done):
    stranger = ActiveReviewerFactory()
    review = ReviewFactory(submission=done, status=ReviewStatus.ASSIGNED)
    web.force_login(stranger.user)

    assert web.get(f"/review/{review.pk}/").status_code == 404


def test_reviewer_panel_has_no_ai_section_when_the_flag_is_off(web, done, competition):
    reviewer = ActiveReviewerFactory()
    review = ReviewFactory(submission=done, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    competition.feature_flags = {**competition.feature_flags, "ai_grading": False}
    competition.save(update_fields=["feature_flags"])
    web.force_login(reviewer.user)

    content = web.get(f"/review/{review.pk}/").content.decode()

    assert "Ocena AI" not in content
    assert SENTINEL_SUMMARY not in content
