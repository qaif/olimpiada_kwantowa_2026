"""Ekrany notatników: bramki (rola, flaga, konkurs), formularz koordynatora, laboratorium uczestnika,
wstawki w karcie zadania i w panelu recenzenta, polityka CSP."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.notebooks import services
from apps.notebooks.models import NotebookMode, NotebookRun, NotebookTask
from apps.tenancy.tests.factories import grant_membership
from apps.web.middleware import NOTEBOOK_LAB_POLICY

from .conftest import GOOD, HIDDEN_SENTINEL, HIDDEN_TESTS, VISIBLE_TESTS, enable, submit

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/notebooks/"


def task_url(problem) -> str:
    return f"/coordinator/notebooks/problems/{problem.pk}/"


def lab_url(problem) -> str:
    return f"/me/notebooks/{problem.pk}/"


@pytest.fixture
def web():
    return Client()


@pytest.fixture
def lab_built(settings, tmp_path):
    root = tmp_path / "lab"
    (root / "314.0.7-abc").mkdir(parents=True)
    (root / "current.json").write_text(
        json.dumps({"build_id": "314.0.7-abc", "transfer_bytes": 20 * 1024 * 1024})
    )
    settings.NOTEBOOK_LAB_DIR = str(root)
    from apps.notebooks import lab

    lab._cache.clear()
    return root


def configured(problem, coordinator, **extra):
    return services.save_task(
        problem,
        actor=coordinator,
        mode=NotebookMode.AUTOGRADED,
        visible_tests=json.dumps(VISIBLE_TESTS),
        hidden_tests=json.dumps(
            [*HIDDEN_TESTS, {"id": "s", "target": "x", "check": "value", "expected": HIDDEN_SENTINEL}]
        ),
        **extra,
    )


# --- bramki ---------------------------------------------------------------------------------------


def test_coordinator_screens_are_404_without_flag(web, coordinator, problem):
    web.force_login(coordinator)
    assert web.get(LIST_URL).status_code == 404
    assert web.get(task_url(problem)).status_code == 404
    assert web.post(task_url(problem), {"action": "save"}).status_code == 404


@pytest.mark.parametrize("flag_on", [False, True])
def test_participant_and_reviewer_cannot_open_coordinator_screens(web, competition, problem, flag_on):
    if flag_on:
        enable(competition)
    participant = ParticipantFactory()
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    for user in (participant.user, ActiveReviewerFactory().user):
        web.force_login(user)
        assert web.get(LIST_URL).status_code == 403
        assert web.get(task_url(problem)).status_code == 403


def test_problem_of_another_competition_is_404(web, competition, coordinator, other_competition):
    enable(competition)
    foreign = ProblemFactory(stage=StageFactory(competition=other_competition), competition=other_competition)
    web.force_login(coordinator)
    assert web.get(task_url(foreign)).status_code == 404


def test_list_and_nav_item(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    response = web.get(LIST_URL)
    assert response.status_code == 200
    html = response.content.decode()
    assert problem.title in html and "Notatniki kwantowe" in html


def test_nav_item_absent_without_flag(web, coordinator):
    web.force_login(coordinator)
    assert "Notatniki kwantowe" not in web.get("/coordinator/").content.decode()


# --- formularz koordynatora -----------------------------------------------------------------------


def test_coordinator_saves_task_with_files(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    starter = SimpleUploadedFile("start.ipynb", json.dumps(GOOD).encode(), "application/json")
    response = web.post(
        task_url(problem),
        {
            "action": "save",
            "mode": "AUTOGRADED",
            "language": "en",
            "time_limit_seconds": 15,
            "memory_limit_mb": 512,
            "results_visibility": "STAFF",
            "visible_tests": json.dumps(VISIBLE_TESTS),
            "hidden_tests": json.dumps(HIDDEN_TESTS),
            "starter_file": starter,
        },
    )
    assert response.status_code == 302
    task = NotebookTask.objects.get(problem=problem)
    assert task.language == "en" and task.starter_notebook["cells"][1]["source"].startswith("qc =")
    assert len(task.hidden_tests) == 2


def test_invalid_tests_rerender_form_with_error(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    response = web.post(
        task_url(problem),
        {"action": "save", "mode": "AUTOGRADED", "language": "pl", "time_limit_seconds": 20,
         "memory_limit_mb": 512, "results_visibility": "STAFF", "hidden_tests": "[{\"check\": \"nope\"}]"},
    )  # fmt: skip
    assert response.status_code == 400
    assert "check" in response.content.decode()
    assert not NotebookTask.objects.exists()


def test_bad_starter_file_is_refused(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    response = web.post(
        task_url(problem),
        {"action": "save", "mode": "FREE", "language": "pl", "time_limit_seconds": 20, "memory_limit_mb": 512,
         "results_visibility": "STAFF",
         "starter_file": SimpleUploadedFile("x.ipynb", b"{nie json", "application/json")},
    )  # fmt: skip
    assert response.status_code == 400


def test_reference_run_and_results_page(
    web, competition, coordinator, problem, participant, django_capture_on_commit_callbacks
):
    enable(competition)
    configured(problem, coordinator, reference_notebook=GOOD)
    web.force_login(coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        assert web.post(task_url(problem), {"action": "reference"}).status_code == 302
    page = web.get(task_url(problem)).content.decode()
    assert "Stan Bella" in page
    submission = submit(problem, participant, GOOD)
    with django_capture_on_commit_callbacks(execute=True):
        services.pump()
    results = web.get(f"/coordinator/notebooks/problems/{problem.pk}/results/")
    assert participant.public_code in results.content.decode()
    csv = web.get(f"/coordinator/notebooks/problems/{problem.pk}/results/?format=csv")
    assert csv["Content-Type"].startswith("text/csv")
    run = NotebookRun.objects.get(submission=submission)
    assert web.get(f"/coordinator/notebooks/runs/{run.pk}/").status_code == 200


# --- uczestnik ------------------------------------------------------------------------------------


def test_participant_lab_page_and_csp(web, competition, coordinator, problem, participant, lab_built):
    enable(competition)
    configured(problem, coordinator)
    web.force_login(participant.user)
    response = web.get(lab_url(problem))
    assert response.status_code == 200
    html = response.content.decode()
    assert "/static/notebook-lab/314.0.7-abc/lab/index.html?fromURL=/me/notebooks/" in html
    policy = response["Content-Security-Policy"]
    frame_src = next(d for d in policy.split("; ") if d.startswith("frame-src"))
    assert "'self'" in frame_src
    assert "unsafe-eval" not in policy  # strona zadania ma politykę serwisu, nie laboratorium
    # Pozostałe strony serwisu nie dostają 'self' w frame-src.
    other = web.get("/me/")["Content-Security-Policy"]
    assert "'self'" not in next(d for d in other.split("; ") if d.startswith("frame-src"))


def test_starter_notebook_for_participant_has_no_hidden_tests(
    web, competition, coordinator, problem, participant
):
    enable(competition)
    task = configured(problem, coordinator)
    web.force_login(participant.user)
    response = web.get(f"/me/notebooks/{problem.pk}/starter/{services.starter_filename(task)}")
    assert response.status_code == 200
    body = response.content.decode()
    assert HIDDEN_SENTINEL not in body
    assert "check(TESTS" in body
    assert response["Cache-Control"] == "no-store"
    assert web.get(f"/me/notebooks/{problem.pk}/starter/other.ipynb").status_code == 404


def test_hidden_tests_never_reach_participant_html(
    web, competition, coordinator, problem, participant, lab_built
):
    enable(competition)
    configured(problem, coordinator)
    web.force_login(participant.user)
    for url in (lab_url(problem), "/me/"):
        assert HIDDEN_SENTINEL not in web.get(url).content.decode()


def test_participant_gates(web, competition, coordinator, problem, participant):
    other = ParticipantFactory()
    grant_membership(other.user, competition, CompetitionRole.PARTICIPANT)
    enable(competition)
    configured(problem, coordinator)
    # Bez wpisu na etap – 404.
    web.force_login(other.user)
    assert web.get(lab_url(problem)).status_code == 404
    # Etap jeszcze nieotwarty – 404 (treść zadania nie jest jawna).
    stage = problem.stage
    stage.opens_at = timezone.now() + timezone.timedelta(days=1)
    stage.deadline_at = stage.opens_at + timezone.timedelta(days=1)
    stage.save()
    web.force_login(participant.user)
    assert web.get(lab_url(problem)).status_code == 404
    # Flaga wyłączona – 404.
    competition.feature_flags = {**competition.feature_flags, "quantum_notebooks": False}
    competition.save(update_fields=["feature_flags"])
    assert web.get(lab_url(problem)).status_code == 404


def test_problem_card_shows_lab_button_only_with_flag(
    web, competition, coordinator, problem, participant, lab_built
):
    web.force_login(participant.user)
    assert lab_url(problem) not in web.get("/me/").content.decode()
    enable(competition)
    configured(problem, coordinator)
    html = web.get("/me/").content.decode()
    assert lab_url(problem) in html
    assert "20.0 MB" in html or "20,0 MB" in html


def test_reviewer_panel_shows_points(
    web, competition, coordinator, problem, participant, django_capture_on_commit_callbacks
):
    from apps.accounts.tests.factories import ActiveReviewerFactory as Reviewer
    from apps.grading.tests.factories import ReviewFactory

    enable(competition)
    configured(problem, coordinator)
    submission = submit(problem, participant, GOOD)
    with django_capture_on_commit_callbacks(execute=True):
        services.pump()
    reviewer = Reviewer()
    grant_membership(reviewer.user, competition, CompetitionRole.REVIEWER)
    review = ReviewFactory(submission=submission, reviewer=reviewer)
    web.force_login(reviewer.user)
    response = web.get(f"/review/{review.pk}/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Testy automatyczne" in html and "Stan Bella" in html
    assert participant.user.email not in html


# --- CSP ścieżki laboratorium ---------------------------------------------------------------------


def test_lab_path_gets_lab_policy(settings, tmp_path, web):
    from apps.web.middleware import is_notebook_lab_path

    assert is_notebook_lab_path(f"{settings.STATIC_URL}notebook-lab/314/lab/index.html")
    assert not is_notebook_lab_path(f"{settings.STATIC_URL}css/app.css")
    assert "'wasm-unsafe-eval'" in NOTEBOOK_LAB_POLICY and "frame-ancestors 'self'" in NOTEBOOK_LAB_POLICY
    assert "https:" not in NOTEBOOK_LAB_POLICY  # żadnego zewnętrznego hosta
    # Dowolna odpowiedź spod ścieżki (także 404) dostaje politykę laboratorium, a nie serwisu.
    response = web.get(f"{settings.STATIC_URL}notebook-lab/nie-ma/index.html")
    assert response["Content-Security-Policy"] == NOTEBOOK_LAB_POLICY
