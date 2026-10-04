"""Serwisy notatników: konfiguracja, przepływ oceny przez spool (inline), widoczność, przeliczanie."""

from __future__ import annotations

import json

import pytest

from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.notebooks import notebook_io, services, spool
from apps.notebooks.models import NotebookMode, NotebookRun, ResultsVisibility, RunStatus

from .conftest import GOOD, HIDDEN_SENTINEL, HIDDEN_TESTS, VISIBLE_TESTS, WRONG, enable, notebook, submit

pytestmark = pytest.mark.django_db


@pytest.fixture
def committed(django_capture_on_commit_callbacks):
    """Zadania Celery (eager) wychodzą w ``on_commit`` – w teście wykonujemy je od razu."""

    def run(function, *args, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return function(*args, **kwargs)

    return run


def autograded(problem, coordinator, **extra):
    return services.save_task(
        problem,
        actor=coordinator,
        mode=NotebookMode.AUTOGRADED,
        visible_tests=json.dumps(VISIBLE_TESTS),
        hidden_tests=json.dumps(HIDDEN_TESTS),
        **extra,
    )


def test_save_requires_flag(competition, problem, coordinator):
    with pytest.raises(DomainError) as excinfo:
        autograded(problem, coordinator)
    assert excinfo.value.machine_code == "NOTEBOOKS_DISABLED"


def test_save_validates_tests_and_format(competition, problem, coordinator):
    enable(competition)
    with pytest.raises(DomainError) as excinfo:
        services.save_task(problem, actor=coordinator, mode=NotebookMode.AUTOGRADED, hidden_tests="[{]")
    assert excinfo.value.machine_code == "NOTEBOOK_TESTS_INVALID"
    problem.allowed_formats = ["pdf"]
    problem.save()
    with pytest.raises(DomainError) as excinfo:
        autograded(problem, coordinator)
    assert excinfo.value.machine_code == "NOTEBOOK_FORMAT_MISSING"


def test_save_normalises_and_audits(competition, problem, coordinator):
    enable(competition)
    task = autograded(problem, coordinator)
    assert task.hidden_tests[0]["target"] == {"name": "qc"}
    assert AuditLog.objects.filter(action="notebooks.task_saved", target_id=str(task.pk)).exists()


def test_end_to_end_grading_through_the_sandbox(competition, problem, coordinator, participant, committed):
    enable(competition)
    autograded(problem, coordinator)
    good = submit(problem, participant, GOOD)

    result = committed(services.pump)

    assert result["created"] == 1
    run = NotebookRun.objects.get(submission=good)
    assert run.status == RunStatus.DONE, (run.error_code, run.cell_errors)
    assert [item["passed"] for item in run.results] == [True, True]
    assert run.score == 4 and run.max_score == 4
    assert run.visible_results[0]["passed"] is True


def test_wrong_solution_scores_partially(competition, problem, coordinator, participant, committed):
    enable(competition)
    autograded(problem, coordinator)
    wrong = submit(problem, participant, WRONG)
    committed(services.pump)
    run = NotebookRun.objects.get(submission=wrong)
    assert run.status == RunStatus.DONE
    passed = {item["id"]: item["passed"] for item in run.results}
    assert passed == {"bell": False, "small": True}  # zły stan, ale głębokość 2 mieści się w limicie
    assert run.score == 1


def test_only_latest_clean_version_is_graded(competition, problem, coordinator, participant, committed):
    enable(competition)
    autograded(problem, coordinator)
    submit(problem, participant, WRONG, version=1)
    submit(problem, participant, WRONG, version=2)
    newest = submit(problem, participant, GOOD, version=3, clean=False)
    # Najnowsza wersja czeka na skan – starszych nie sprawdzamy (to nie one pójdą do oceny).
    assert committed(services.pump)["created"] == 0
    newest.files.update(av_status="CLEAN")
    assert committed(services.pump)["created"] == 1
    assert list(NotebookRun.objects.values_list("submission_id", flat=True)) == [newest.pk]


def test_free_mode_and_flag_off_create_no_runs(competition, problem, coordinator, participant, committed):
    enable(competition)
    services.save_task(problem, actor=coordinator, mode=NotebookMode.FREE)
    submit(problem, participant, GOOD)
    assert committed(services.pump)["created"] == 0
    task = services.task_for(problem)
    task.mode = NotebookMode.AUTOGRADED
    task.hidden_tests = HIDDEN_TESTS
    task.save()
    competition.feature_flags = {**competition.feature_flags, "quantum_notebooks": False}
    competition.save(update_fields=["feature_flags"])
    assert committed(services.pump)["created"] == 0


def test_job_carries_targets_but_never_expectations(competition, problem, coordinator):
    enable(competition)
    task = services.save_task(
        problem,
        actor=coordinator,
        mode=NotebookMode.AUTOGRADED,
        hidden_tests=json.dumps([{"id": "s", "target": "x", "check": "value", "expected": HIDDEN_SENTINEL}]),
    )
    job = services.build_job(task, GOOD)
    assert HIDDEN_SENTINEL not in json.dumps(job)
    assert job["targets"] == [{"key": '{"name": "x"}', "name": "x"}]


def test_starter_notebook_has_visible_tests_only(competition, problem, coordinator):
    enable(competition)
    task = services.save_task(
        problem,
        actor=coordinator,
        mode=NotebookMode.AUTOGRADED,
        visible_tests=json.dumps(VISIBLE_TESTS),
        hidden_tests=json.dumps([{"id": "s", "target": "x", "check": "value", "expected": HIDDEN_SENTINEL}]),
    )
    starter = services.starter_notebook(task)
    text = json.dumps(starter)
    assert HIDDEN_SENTINEL not in text
    last = starter["cells"][-1]
    assert notebook_io.VISIBLE_TESTS_TAG in last["metadata"]["tags"]
    assert "check(TESTS" in last["source"]
    # Komórka testów widocznych jest poprawnym Pythonem i nie trafia do oceny na serwerze.
    compile(last["source"], "<cell>", "exec")
    assert last["source"] not in notebook_io.code_cells(starter)


def test_uploaded_starter_outputs_are_cleared(competition, problem, coordinator):
    enable(competition)
    nb = notebook("x = 1")
    nb["cells"][0]["outputs"] = [{"output_type": "stream", "name": "stdout", "text": "rozwiązanie: 42"}]
    task = services.save_task(problem, actor=coordinator, mode=NotebookMode.FREE, starter_notebook=nb)
    starter = services.starter_notebook(task)
    assert "rozwiązanie: 42" not in json.dumps(starter)


def test_runner_unavailable_is_an_error_not_a_hang(
    competition, problem, coordinator, participant, settings, tmp_path, committed
):
    enable(competition)
    autograded(problem, coordinator)
    settings.NOTEBOOK_RUNNER_INLINE = False
    settings.NOTEBOOK_SPOOL_DIR = str(tmp_path / "missing")
    submission = submit(problem, participant, GOOD)
    committed(services.pump)
    run = NotebookRun.objects.get(submission=submission)
    assert run.status == RunStatus.ERROR and run.error_code == "runner_unavailable"


def test_collect_times_out_without_result(
    competition, problem, coordinator, participant, settings, tmp_path, committed
):
    from datetime import timedelta

    from django.utils import timezone

    enable(competition)
    autograded(problem, coordinator)
    settings.NOTEBOOK_RUNNER_INLINE = False
    (tmp_path / "spool2" / "jobs").mkdir(parents=True)
    settings.NOTEBOOK_SPOOL_DIR = str(tmp_path / "spool2")
    submission = submit(problem, participant, GOOD)
    committed(services.pump)
    run = NotebookRun.objects.get(submission=submission)
    assert run.status == RunStatus.RUNNING
    assert (tmp_path / "spool2" / "jobs" / f"{run.job_id}.json").exists()
    assert services.collect_run(run.pk) == "wait"
    later = timezone.now() + timedelta(
        seconds=run.task.time_limit_seconds + services.RUNNER_GRACE_SECONDS + 1
    )
    assert services.collect_run(run.pk, now=later) == "timeout"
    run.refresh_from_db()
    assert run.status == RunStatus.ERROR and run.error_code == "runner_timeout"
    assert not (tmp_path / "spool2" / "jobs" / f"{run.job_id}.json").exists()


def test_spool_fetch_rejects_path_tricks(settings, tmp_path):
    settings.NOTEBOOK_SPOOL_DIR = str(tmp_path)
    assert spool.fetch("../../etc/passwd") is None
    assert spool.fetch("") is None


def test_rerun_all_after_test_change(competition, problem, coordinator, participant, committed):
    enable(competition)
    task = autograded(problem, coordinator)
    submission = submit(problem, participant, GOOD)
    committed(services.pump)
    task.hidden_tests = [{**HIDDEN_TESTS[0], "points": 10}]
    task.save()
    assert committed(services.rerun_all, task, actor=coordinator) == 1
    run = NotebookRun.objects.get(submission=submission)
    assert run.status == RunStatus.DONE and run.max_score == 10
    assert AuditLog.objects.filter(action="notebooks.rerun_all").exists()


def test_reference_run(competition, problem, coordinator, committed):
    enable(competition)
    task = autograded(problem, coordinator, reference_notebook=GOOD)
    run = committed(services.run_reference, task, actor=coordinator)
    run.refresh_from_db()
    assert run.is_reference and run.status == RunStatus.DONE and run.score == run.max_score
    assert services.latest_runs(task) == []  # wzorzec nie jest wierszem wyników uczestników


def test_participant_visibility(competition, problem, coordinator, participant, committed):
    enable(competition)
    task = autograded(problem, coordinator)
    submission = submit(problem, participant, GOOD)
    committed(services.pump)
    assert services.participant_results(task, submission) is None  # STAFF – domyślnie
    task.results_visibility = ResultsVisibility.AFTER_CLOSE
    task.save()
    assert services.participant_results(task, submission) is None
    from django.utils import timezone

    stage = problem.stage
    stage.closed_at = timezone.now()
    stage.save(update_fields=["closed_at"])
    submission.refresh_from_db()
    assert services.participant_results(task, submission) is not None
    task.results_visibility = ResultsVisibility.IMMEDIATE
    task.save()
    assert services.participant_results(task, submission).status == RunStatus.DONE


def test_results_csv(competition, problem, coordinator, participant, committed):
    enable(competition)
    task = autograded(problem, coordinator)
    submit(problem, participant, GOOD)
    committed(services.pump)
    text = services.results_csv(task)
    header, row = text.strip().splitlines()
    assert header.startswith("participant_code,version,status,test:bell,test:small")
    assert participant.public_code in row and row.endswith("4.00,4.00")


def test_malicious_notebook_is_contained_end_to_end(
    competition, problem, coordinator, participant, committed
):
    enable(competition)
    autograded(problem, coordinator)
    evil = notebook(
        "import socket\ns = socket.socket()",
        "open('/app/config/settings/base.py').read()",
        "import os\nos.system('id')",
        "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)",
    )
    submission = submit(problem, participant, evil)
    committed(services.pump)
    run = NotebookRun.objects.get(submission=submission)
    assert run.status == RunStatus.DONE
    assert len(run.cell_errors) == 3
    assert all("not allowed" in item["error"] for item in run.cell_errors)
    assert run.score == 4  # reszta notatnika liczy się normalnie
