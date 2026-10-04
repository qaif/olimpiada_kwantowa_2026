"""Ekrany notatników: bramki (rola, flaga, konkurs), formularz koordynatora, laboratorium uczestnika,
wstawki w karcie zadania i w panelu recenzenta, polityka CSP."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory, UserFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.notebooks import services
from apps.notebooks.models import NotebookMode, NotebookRun, NotebookTask
from apps.tenancy.tests.factories import grant_membership
from apps.web.middleware import NOTEBOOK_LAB_HEADERS, build_notebook_lab_policy

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


def test_participant_lab_page_opens_lab_in_new_tab_without_frame(
    web, competition, coordinator, problem, participant, lab_built
):
    enable(competition)
    configured(problem, coordinator)
    web.force_login(participant.user)
    response = web.get(lab_url(problem))
    assert response.status_code == 200
    html = response.content.decode()
    assert "<iframe" not in html
    assert 'href="/static/notebook-lab/314.0.7-abc/lab/index.html?fromURL=/notebook-starter/' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    # Strona zadania ma zwykłą politykę serwisu – co do bajtu tę, co /me/ (bez wyjątków dla ramek).
    policy = response["Content-Security-Policy"]
    frame_src = next(d for d in policy.split("; ") if d.startswith("frame-src"))
    assert "'self'" not in frame_src and "unsafe-eval" not in policy


def starter_path(problem, participant):
    task = services.task_for(problem)
    return services.starter_url(task, participant, participant.user)


def test_starter_notebook_for_participant_has_no_hidden_tests(
    web, competition, coordinator, problem, participant
):
    enable(competition)
    task = configured(problem, coordinator)
    web.force_login(participant.user)
    path = starter_path(problem, participant)
    assert path.startswith("/notebook-starter/") and path.endswith(services.starter_filename(task))
    response = web.get(path)
    assert response.status_code == 200
    body = response.content.decode()
    assert HIDDEN_SENTINEL not in body
    assert "check(TESTS" in body
    assert response["Cache-Control"] == "no-store"
    token = path.split("/")[2]
    assert web.get(f"/notebook-starter/{token}/other.ipynb").status_code == 404
    assert web.get(f"/notebook-starter/{token}x/{services.starter_filename(task)}").status_code == 404


def test_starter_token_is_bound_to_the_account(web, competition, coordinator, problem, participant):
    enable(competition)
    configured(problem, coordinator)
    path = starter_path(problem, participant)
    other = ParticipantFactory(user=UserFactory(email="inny@example.test", groups=["participant"]))
    grant_membership(other.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(participant=other, stage=problem.stage)
    web.force_login(other.user)
    assert web.get(path).status_code == 404
    web.logout()
    assert web.get(path).status_code == 404


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


def test_lab_path_gets_path_restricted_policy_and_isolation_headers(settings, web):
    from apps.web.middleware import is_notebook_lab_path

    assert is_notebook_lab_path(f"{settings.STATIC_URL}notebook-lab/314/lab/index.html")
    assert not is_notebook_lab_path(f"{settings.STATIC_URL}css/app.css")
    # Dowolna odpowiedź spod ścieżki (także 404) dostaje politykę laboratorium, a nie serwisu.
    response = web.get(f"{settings.STATIC_URL}notebook-lab/nie-ma/index.html")
    policy = response["Content-Security-Policy"]
    assert policy == build_notebook_lab_policy("http://testserver")
    directives = dict(item.split(" ", 1) for item in policy.split("; "))
    lab = f"http://testserver{settings.STATIC_URL}notebook-lab/"
    # Każde źródło zawężone do ścieżki laboratorium – żadnego 'self' (całego originu).
    assert "'self'" not in policy
    assert directives["connect-src"] == f"{lab} http://testserver/notebook-starter/"
    assert directives["frame-src"] == f"{lab} blob:"
    assert directives["form-action"] == "'none'" and directives["frame-ancestors"] == "'none'"
    for name, value in NOTEBOOK_LAB_HEADERS.items():
        assert response[name] == value


def test_lab_policy_blocks_platform_api_by_path():
    """Ścieżki API i paneli nie pasują do żadnego źródła ``connect-src`` (CSP porównuje ścieżkę)."""
    policy = build_notebook_lab_policy("https://olimpiada.example")
    connect = next(d for d in policy.split("; ") if d.startswith("connect-src")).split()[1:]
    for path in ("/api/submissions/", "/me/", "/coordinator/", "/accounts/email/", "/"):
        url = f"https://olimpiada.example{path}"
        assert not any(url.startswith(source) for source in connect), path


@pytest.mark.parametrize(
    ("method", "path", "dest", "status"),
    [
        ("post", "/me/", "", 403),
        ("post", "/coordinator/notebooks/", "", 403),
        ("get", "/api/competitions/", "", 403),
        ("get", "/me/", "empty", 403),
        ("get", "/me/", "document", 200),
    ],
)
def test_server_guard_refuses_requests_from_the_lab(web, participant, method, path, dest, status):
    web.force_login(participant.user)
    referer = "http://testserver/static/notebook-lab/314/lab/index.html"
    headers = {"HTTP_REFERER": referer}
    if dest:
        headers["HTTP_SEC_FETCH_DEST"] = dest
    response = getattr(web, method)(path, **headers)
    if status == 403:
        assert response.status_code == 403
    else:
        assert response.status_code != 403
    # Ten sam adres z ``Referer`` spoza laboratorium – strażnik się nie wtrąca.
    assert getattr(web, method)(path, HTTP_REFERER="http://testserver/me/").content != (
        b"Requests from the notebook lab to the platform are not allowed."
    )


def test_coordinator_screens_warn_about_same_origin(web, competition, coordinator, problem):
    enable(competition)
    web.force_login(coordinator)
    for url in (LIST_URL, task_url(problem)):
        html = web.get(url).content.decode()
        assert "laboratorium działa w domenie serwisu" in html and "§ 40.6" in html
