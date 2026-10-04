"""Logika notatników kwantowych: konfiguracja zadania, notatnik startowy i przebiegi oceny.

Reguły ról i flagi stoją **tutaj**, a nie tylko w widokach i szablonach (QC-01 § 7): serwis
rzuca ``DomainError`` przy zadaniu bez notatnika, z wyłączoną flagą albo w złym trybie, więc ten sam
warunek obowiązuje panel, zadania Celery i każdy przyszły punkt wejścia.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import AvStatus, Submission, SubmissionFile
from apps.submissions.storage import get_submission_storage
from qclab import grader

from . import notebook_io, spool
from .models import NotebookMode, NotebookRun, NotebookTask, ResultsVisibility, RunStatus

logger = logging.getLogger(__name__)

FEATURE = "quantum_notebooks"
IPYNB_MIME = "application/x-ipynb+json"
#: Zapas ponad limit czasu zadania, po którym brak wyniku z piaskownicy znaczy „nie działa”.
RUNNER_GRACE_SECONDS = 60
#: Przebieg ``RUNNING`` starszy niż to jest zgubiony (restart workera między wysyłką a odbiorem).
STALE_RUNNING = timedelta(minutes=15)
MAX_SUBMISSION_BYTES = 25 * 1024 * 1024
OUTPUT_TAIL_CHARS = 4000

#: Komunikaty błędów przebiegu. ``gettext_noop`` – w bazie leży kod, a tekst w języku oglądającego
#: składa ``error_text`` w chwili wyświetlenia.
ERROR_MESSAGES = {
    "timeout": gettext_noop("Przekroczono limit czasu wykonania notatnika."),
    "memory": gettext_noop("Przekroczono limit pamięci."),
    "crashed": gettext_noop("Proces notatnika zakończył się awarią."),
    "invalid_notebook": gettext_noop("Plik nie jest poprawnym notatnikiem Jupytera."),
    "runner_unavailable": gettext_noop("Środowisko sprawdzania jest niedostępne."),
    "runner_timeout": gettext_noop("Środowisko sprawdzania nie odpowiedziało na czas."),
    "storage": gettext_noop("Nie udało się odczytać pliku pracy."),
    "invalid_tests": gettext_noop("Testy zadania są nieprawidłowe – popraw je w panelu."),
    "error": gettext_noop("Błąd środowiska sprawdzania."),
}


def error_text(code: str) -> str:
    return _(ERROR_MESSAGES.get(code, ERROR_MESSAGES["error"]))


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def is_enabled(competition) -> bool:
    return competition is not None and competition.has_feature(FEATURE)


def task_for(problem) -> NotebookTask | None:
    try:
        return problem.notebook_task
    except NotebookTask.DoesNotExist:
        return None


def tests_hash(task: NotebookTask) -> str:
    payload = json.dumps([task.visible_tests, task.hidden_tests], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# --- konfiguracja (koordynator) -------------------------------------------------------------------


def validate_tests(raw, label: str) -> list[dict]:
    try:
        return grader.validate_tests(raw)
    except grader.SpecError as exc:
        raise _bad_request(f"{label}: {exc}", "NOTEBOOK_TESTS_INVALID") from exc


@transaction.atomic
def save_task(problem, *, actor, request=None, **fields) -> NotebookTask:
    """Zapisuje ustawienia notatnika zadania. Testy są walidowane i zapisywane w postaci znormalizowanej."""
    competition = problem.stage.edition.competition
    if not is_enabled(competition):
        raise _conflict(_("Notatniki kwantowe są w tym konkursie wyłączone."), "NOTEBOOKS_DISABLED")
    task = task_for(problem) or NotebookTask(problem=problem)
    for name in ("visible_tests", "hidden_tests"):
        if name in fields:
            label = _("Testy widoczne") if name == "visible_tests" else _("Testy ukryte")
            fields[name] = validate_tests(fields[name], str(label))
    for name, value in fields.items():
        setattr(task, name, value)
    if task.mode == NotebookMode.AUTOGRADED:
        if "ipynb" not in (problem.allowed_formats or []):
            raise _bad_request(
                _(
                    "Zadanie sprawdzane automatycznie musi przyjmować pliki .ipynb – dopisz ten format "
                    "w zadaniu."
                ),
                "NOTEBOOK_FORMAT_MISSING",
            )
        if not task.hidden_tests and not task.visible_tests:
            raise _bad_request(
                _("Zadanie sprawdzane automatycznie potrzebuje co najmniej jednego testu."),
                "NOTEBOOK_NO_TESTS",
            )
    task.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    task.save()
    audit(
        actor,
        "notebooks.task_saved",
        task,
        {
            "problem": problem.pk,
            "mode": task.mode,
            "visible_tests": len(task.visible_tests),
            "hidden_tests": len(task.hidden_tests),
            "visibility": task.results_visibility,
        },
        request=request,
    )
    return task


def delete_task(task: NotebookTask, *, actor, request=None) -> None:
    audit(actor, "notebooks.task_deleted", task, {"problem": task.problem_id}, request=request)
    task.delete()


def starter_notebook(task: NotebookTask) -> dict:
    """Notatnik startowy dokładnie taki, jak dostaje go uczestnik (z testami widocznymi)."""
    problem = task.problem
    title = (
        f"{problem.number}. {problem.display_title}" if hasattr(problem, "display_title") else problem.title
    )
    return notebook_io.starter_for_participant(
        task.starter_notebook, title, task.language, task.visible_tests
    )


def starter_filename(task: NotebookTask) -> str:
    problem = task.problem
    return f"zadanie-{problem.stage_id}-{problem.number}.ipynb"


STARTER_SALT = "notebooks.starter"
#: Ważność adresu notatnika startowego. Laboratorium pobiera go raz, przy otwarciu.
STARTER_MAX_AGE = 12 * 3600


def starter_url(task: NotebookTask, participant, user) -> str:
    """Adres notatnika startowego dla laboratorium – ścieżka stała, bez prefiksu konkursu."""
    from django.core import signing

    from apps.web.middleware import NOTEBOOK_STARTER_PATH

    competition_id = task.problem.stage.edition.competition_id
    token = signing.dumps({"c": competition_id, "t": task.pk, "u": user.pk}, salt=STARTER_SALT, compress=True)
    return f"{NOTEBOOK_STARTER_PATH}{token}/{starter_filename(task)}"


def task_from_starter_token(token: str, user, now=None) -> NotebookTask | None:
    """Zadanie z tokenu – albo ``None`` (podpis, termin, inne konto, bramki uczestnika)."""
    from django.core import signing

    from apps.accounts.services import participant_for

    try:
        data = signing.loads(token, salt=STARTER_SALT, max_age=STARTER_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("u") != user.pk:
        return None
    task = (
        NotebookTask.objects.select_related("problem__stage__edition__competition")
        .filter(pk=data.get("t"), problem__stage__edition__competition_id=data.get("c"))
        .first()
    )
    if task is None:
        return None
    competition = task.problem.stage.edition.competition
    participant = participant_for(user, competition)
    if participant is None:
        return None
    try:
        return participant_task(task.problem, participant, now)
    except DomainError:
        return None


def participant_task(problem, participant, now=None) -> NotebookTask:
    """Notatnik dla uczestnika: flaga, wpis na etap, treść zadania już jawna. Inaczej 404-owy błąd."""
    from apps.competitions.models import StageEntry

    competition = problem.stage.edition.competition
    task = task_for(problem)
    not_found = DomainError(_("Nie ma takiego notatnika."), "NOTEBOOK_NOT_FOUND", http.HTTP_404_NOT_FOUND)
    if task is None or not is_enabled(competition):
        raise not_found
    if not StageEntry.objects.filter(participant=participant, stage=problem.stage).exists():
        raise not_found
    if not problem.stage.has_opened(now):
        raise not_found
    return task


# --- przebiegi oceny ------------------------------------------------------------------------------


def _latest_versions(problem_ids) -> dict[tuple[int, int], int]:
    from django.db.models import Max

    rows = (
        Submission.objects.filter(problem_id__in=problem_ids)
        .values("entry_id", "problem_id")
        .annotate(top=Max("version"))
    )
    return {(row["entry_id"], row["problem_id"]): row["top"] for row in rows}


def pump(now=None) -> dict[str, int]:
    """Beat: nowe przebiegi dla najnowszych czystych plików ``.ipynb`` + domknięcie zgubionych."""
    now = now or timezone.now()
    stale = list(
        NotebookRun.objects.filter(status=RunStatus.RUNNING, started_at__lt=now - STALE_RUNNING).values_list(
            "pk", flat=True
        )
    )
    for run_id in stale:
        mark_error(run_id, "runner_timeout")
    tasks = [
        task
        for task in NotebookTask.objects.filter(mode=NotebookMode.AUTOGRADED).select_related(
            "problem__stage__edition__competition"
        )
        if is_enabled(task.problem.stage.edition.competition)
    ]
    if not tasks:
        return {"created": 0, "recovered": len(stale)}
    by_problem = {task.problem_id: task for task in tasks}
    candidates = (
        SubmissionFile.objects.filter(
            submission__problem_id__in=list(by_problem),
            av_status=AvStatus.CLEAN,
            mime=IPYNB_MIME,
        )
        .exclude(pk__in=NotebookRun.objects.filter(submission_file__isnull=False).values("submission_file"))
        .select_related("submission")
    )
    latest = _latest_versions(list(by_problem))
    created = []
    for item in candidates:
        submission = item.submission
        if latest.get((submission.entry_id, submission.problem_id)) != submission.version:
            continue
        task = by_problem[submission.problem_id]
        with transaction.atomic():
            NotebookRun.objects.filter(
                task=task,
                submission__entry_id=submission.entry_id,
                status=RunStatus.PENDING,
                is_reference=False,
            ).update(status=RunStatus.SUPERSEDED)
            run = NotebookRun.objects.create(
                competition_id=submission.competition_id,
                task=task,
                submission=submission,
                submission_file=item,
                tests_hash=tests_hash(task),
            )
        created.append(run.pk)
    for run_id in created:
        _enqueue(run_id)
    return {"created": len(created), "recovered": len(stale)}


def _enqueue(run_id: int) -> None:
    from .tasks import run_notebook

    transaction.on_commit(lambda: run_notebook.delay(run_id))


def mark_error(run_id: int, code: str, detail: str = "") -> None:
    NotebookRun.objects.filter(pk=run_id).exclude(status__in=(RunStatus.DONE, RunStatus.SUPERSEDED)).update(
        status=RunStatus.ERROR,
        error_code=code,
        error_message=(detail or ERROR_MESSAGES.get(code, ERROR_MESSAGES["error"]))[:500],
        finished_at=timezone.now(),
    )


def _read_notebook(run: NotebookRun) -> dict:
    if run.is_reference:
        if not run.task.reference_notebook:
            raise notebook_io.NotebookError("no reference notebook")
        return notebook_io.load(run.task.reference_notebook)
    file = run.submission_file
    if file is None or not file.object_key or file.av_status != AvStatus.CLEAN:
        raise notebook_io.NotebookError("file not available")
    stream = get_submission_storage().open(file.object_key)
    try:
        data = stream.read(MAX_SUBMISSION_BYTES + 1)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if len(data) > MAX_SUBMISSION_BYTES:
        raise notebook_io.NotebookError("too large")
    return notebook_io.load(data)


def build_job(task: NotebookTask, notebook: dict) -> dict:
    """Zadanie dla piaskownicy: komórki ucznia i **same cele** testów – bez oczekiwań (§ 9)."""
    tests = [*task.visible_tests, *task.hidden_tests]
    return {
        "cells": notebook_io.code_cells(notebook),
        "targets": grader.targets_of(tests),
        "limits": {
            "wall": int(task.time_limit_seconds) + 10,
            "cpu": int(task.time_limit_seconds),
            "memory_mb": int(task.memory_limit_mb),
        },
    }


def start_run(run_id: int) -> str:
    """Wysyła przebieg do piaskownicy. Zwraca ``"sent"``, ``"skipped"`` albo ``"error"``."""
    run = NotebookRun.objects.select_related("task__problem", "submission_file").filter(pk=run_id).first()
    if run is None or run.status != RunStatus.PENDING:
        return "skipped"
    try:
        notebook = _read_notebook(run)
    except notebook_io.NotebookError:
        mark_error(run_id, "invalid_notebook")
        return "error"
    except Exception:  # noqa: BLE001 - storage rzuca własnymi wyjątkami; przebieg nie może wisieć
        logger.exception("Notatnik: nie udało się odczytać pliku przebiegu %s.", run_id)
        mark_error(run_id, "storage")
        return "error"
    try:
        job_id = spool.submit(build_job(run.task, notebook))
    except spool.SpoolUnavailable:
        logger.error(
            "Notatnik: brak katalogu spool %s – kontener piaskownicy nie działa?", settings.NOTEBOOK_SPOOL_DIR
        )
        mark_error(run_id, "runner_unavailable")
        return "error"
    NotebookRun.objects.filter(pk=run_id).update(
        status=RunStatus.RUNNING, job_id=job_id, started_at=timezone.now(), tests_hash=tests_hash(run.task)
    )
    return "sent"


def collect_run(run_id: int, now=None) -> str:
    """Odbiór wyniku: ``"done"``, ``"wait"`` (jeszcze nie ma) albo ``"timeout"``."""
    now = now or timezone.now()
    run = NotebookRun.objects.select_related("task").filter(pk=run_id).first()
    if run is None or run.status != RunStatus.RUNNING:
        return "done"
    result = spool.fetch(run.job_id)
    if result is None:
        limit = run.task.time_limit_seconds + RUNNER_GRACE_SECONDS
        if run.started_at and (now - run.started_at).total_seconds() > limit:
            spool.cancel(run.job_id)
            mark_error(run_id, "runner_timeout")
            return "timeout"
        return "wait"
    finish_run(run, result)
    return "done"


def _outcomes(tests: list[dict], artifacts: dict, language: str) -> list[dict]:
    return [outcome.as_dict() for outcome in grader.evaluate_all(tests, artifacts, language)]


def finish_run(run: NotebookRun, result: dict) -> NotebookRun:
    """Ocena artefaktów testami (ukryte istnieją wyłącznie tutaj) i zapis wyniku."""
    status = result.get("status")
    if status != "ok":
        code = status if status in ERROR_MESSAGES else "error"
        mark_error(run.pk, code)
        run.refresh_from_db()
        return run
    task = run.task
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    try:
        hidden = grader.validate_tests(task.hidden_tests)
        visible = grader.validate_tests(task.visible_tests)
    except grader.SpecError:
        mark_error(run.pk, "invalid_tests")
        run.refresh_from_db()
        return run
    hidden_results = _outcomes(hidden, artifacts, task.language)
    visible_results = _outcomes(visible, artifacts, task.language)
    # Wynik zadania liczą testy ukryte; bez ukrytych (sam zestaw przykładowy) – widoczne.
    scored = hidden_results or visible_results
    score = sum(Decimal(str(item["points"])) for item in scored)
    max_score = sum(Decimal(str(item["max_points"])) for item in scored)
    cell_errors = result.get("cell_errors") if isinstance(result.get("cell_errors"), list) else []
    NotebookRun.objects.filter(pk=run.pk).update(
        status=RunStatus.DONE,
        results=hidden_results,
        visible_results=visible_results,
        score=score,
        max_score=max_score,
        cell_errors=[
            {"cell": int(item.get("cell", 0)), "error": str(item.get("error", ""))[:1000]}
            for item in cell_errors[:50]
            if isinstance(item, dict)
        ],
        output_tail=str(result.get("output") or "")[:OUTPUT_TAIL_CHARS],
        error_code="",
        error_message="",
        finished_at=timezone.now(),
    )
    run.refresh_from_db()
    return run


@transaction.atomic
def rerun_all(task: NotebookTask, *, actor, request=None) -> int:
    """„Przelicz wszystko” po zmianie testów: najnowsze przebiegi prac wracają do kolejki."""
    if not task.is_autograded:
        raise _conflict(_("To zadanie nie jest sprawdzane automatycznie."), "NOTEBOOK_NOT_AUTOGRADED")
    runs = list(
        NotebookRun.objects.filter(task=task, is_reference=False)
        .exclude(status__in=(RunStatus.PENDING, RunStatus.RUNNING, RunStatus.SUPERSEDED))
        .values_list("pk", flat=True)
    )
    NotebookRun.objects.filter(pk__in=runs).update(
        status=RunStatus.PENDING,
        results=[],
        visible_results=[],
        score=None,
        max_score=None,
        error_code="",
        error_message="",
        requested_at=timezone.now(),
        started_at=None,
        finished_at=None,
    )
    for run_id in runs:
        _enqueue(run_id)
    audit(actor, "notebooks.rerun_all", task, {"runs": len(runs)}, request=request)
    return len(runs)


@transaction.atomic
def run_reference(task: NotebookTask, *, actor, request=None) -> NotebookRun:
    if not task.reference_notebook:
        raise _bad_request(_("Najpierw wgraj notatnik wzorcowy."), "NOTEBOOK_NO_REFERENCE")
    if NotebookRun.objects.filter(
        task=task, is_reference=True, status__in=(RunStatus.PENDING, RunStatus.RUNNING)
    ).exists():
        raise _conflict(_("Sprawdzanie wzorca już trwa – poczekaj na wynik."), "NOTEBOOK_REFERENCE_RUNNING")
    run = NotebookRun.objects.create(
        competition=task.problem.stage.edition.competition,
        task=task,
        is_reference=True,
        tests_hash=tests_hash(task),
    )
    audit(actor, "notebooks.reference_run", task, {"run": run.pk}, request=request)
    _enqueue(run.pk)
    return run


def latest_reference_run(task: NotebookTask) -> NotebookRun | None:
    return NotebookRun.objects.filter(task=task, is_reference=True).order_by("-requested_at", "-id").first()


# --- odczyty dla ekranów --------------------------------------------------------------------------


def latest_runs(task: NotebookTask) -> list[NotebookRun]:
    """Najnowszy przebieg każdej pracy (wpisu) – wiersze tabeli wyników koordynatora."""
    runs = (
        NotebookRun.objects.filter(task=task, is_reference=False)
        .exclude(status=RunStatus.SUPERSEDED)
        .select_related("submission__entry__participant")
        .order_by("submission__entry_id", "-submission__version", "-requested_at")
    )
    seen: set[int] = set()
    rows = []
    for run in runs:
        entry_id = run.submission.entry_id
        if entry_id in seen:
            continue
        seen.add(entry_id)
        rows.append(run)
    rows.sort(key=lambda r: r.submission.entry.participant.public_code)
    return rows


def results_csv(task: NotebookTask) -> str:
    tests = grader.validate_tests(task.hidden_tests) or grader.validate_tests(task.visible_tests)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["participant_code", "version", "status", *[f"test:{t['id']}" for t in tests], "score", "max_score"]
    )
    for run in latest_runs(task):
        points = {item["id"]: item["points"] for item in (run.results or run.visible_results or [])}
        writer.writerow(
            [
                run.submission.entry.participant.public_code,
                run.submission.version,
                run.status,
                *[points.get(t["id"], "") for t in tests],
                run.score if run.score is not None else "",
                run.max_score if run.max_score is not None else "",
            ]
        )
    return buffer.getvalue()


def run_for_submission(submission) -> NotebookRun | None:
    return (
        NotebookRun.objects.filter(submission=submission, is_reference=False)
        .exclude(status=RunStatus.SUPERSEDED)
        .order_by("-requested_at", "-id")
        .first()
    )


def participant_results(task: NotebookTask, submission, now=None) -> NotebookRun | None:
    """Przebieg, który wolno pokazać uczestnikowi – albo ``None`` (``results_visibility``, § 7)."""
    if task.results_visibility == ResultsVisibility.STAFF or submission is None:
        return None
    if task.results_visibility == ResultsVisibility.AFTER_CLOSE and submission.entry.stage.closed_at is None:
        return None
    run = run_for_submission(submission)
    return run if run is not None and run.status == RunStatus.DONE else None
