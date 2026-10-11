"""Ekrany notatników kwantowych: panel koordynatora i laboratorium uczestnika (QC-01 § 7).

Kolejność bramek jak w ocenie AI: najpierw rola (mixin – 403 dla innej roli), potem flaga konkursu
(404). Obiekty zawsze przez ``for_competition`` – zadanie z sąsiedniego konkursu daje 404.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.competitions.models import Problem
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin, ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from qclab import grader

from . import lab, notebook_io, render, services
from .forms import TESTS_EXAMPLE, NotebookTaskForm, tests_as_text
from .models import NotebookRun, NotebookTask, RunStatus


class NotebookCoordinatorMixin(CoordinatorRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            if not services.is_enabled(self.competition):
                raise Http404("Notatniki kwantowe są w tym konkursie wyłączone.")
        return super().dispatch(request, *args, **kwargs)

    def problem(self, pk: int) -> Problem:
        return get_object_or_404(
            Problem.objects.for_competition(self.competition).select_related("stage", "stage__edition"),
            pk=pk,
        )


class NotebookListView(NotebookCoordinatorMixin, View):
    template_name = "notebooks/coordinator_list.html"

    def get(self, request):
        edition = current_edition(self.competition)
        problems = (
            list(
                Problem.objects.for_competition(self.competition)
                .filter(stage__edition=edition)
                .select_related("stage", "notebook_task")
                .order_by("stage__opens_at", "stage_id", "number")
            )
            if edition
            else []
        )
        rows = []
        for problem in problems:
            task = services.task_for(problem)
            rows.append(
                {
                    "problem": problem,
                    "task": task,
                    "runs": NotebookRun.objects.filter(task=task, is_reference=False).count() if task else 0,
                }
            )
        return TemplateResponse(
            request, self.template_name, {"rows": rows, "lab_built": lab.build_info() is not None}
        )


class NotebookTaskView(NotebookCoordinatorMixin, ThrottledFormMixin, View):
    template_name = "notebooks/coordinator_task.html"
    throttle_scope = "notebooks"

    def _context(self, problem: Problem, task: NotebookTask | None, form: NotebookTaskForm) -> dict:
        reference = services.latest_reference_run(task) if task else None
        return {
            "problem": problem,
            "task": task,
            "form": form,
            "reference": reference,
            "reference_error": services.error_text(reference.error_code)
            if reference and reference.error_code
            else "",
            "tests_example": TESTS_EXAMPLE,
            "ipynb_allowed": "ipynb" in (problem.allowed_formats or []),
            "checks": grader.CHECKS,
        }

    def _initial(self, task: NotebookTask | None) -> dict:
        if task is None:
            return {"mode": "FREE", "language": "pl", "time_limit_seconds": 20, "memory_limit_mb": 512,
                    "results_visibility": "STAFF"}  # fmt: skip
        return {
            "mode": task.mode,
            "language": task.language,
            "time_limit_seconds": task.time_limit_seconds,
            "memory_limit_mb": task.memory_limit_mb,
            "results_visibility": task.results_visibility,
            "visible_tests": tests_as_text(task.visible_tests),
            "hidden_tests": tests_as_text(task.hidden_tests),
        }

    def get(self, request, pk: int):
        problem = self.problem(pk)
        task = services.task_for(problem)
        form = NotebookTaskForm(initial=self._initial(task))
        return TemplateResponse(request, self.template_name, self._context(problem, task, form))

    def post(self, request, pk: int):
        problem = self.problem(pk)
        task = services.task_for(problem)
        action = request.POST.get("action", "save")
        try:
            if action == "reference":
                if task is None:
                    raise Http404
                services.run_reference(task, actor=request.user, request=request)
                messages.success(
                    request, _("Notatnik wzorcowy poszedł do sprawdzenia – wynik pojawi się poniżej.")
                )
                return redirect(reverse("web:coordinator-notebook-task", args=[problem.pk]) + "#wzorzec")
            if action == "delete":
                if task is not None:
                    services.delete_task(task, actor=request.user, request=request)
                messages.success(request, _("Notatnik zadania został wyłączony."))
                return redirect("web:coordinator-notebooks")
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect("web:coordinator-notebook-task", pk=problem.pk)
        form = NotebookTaskForm(request.POST, request.FILES)
        if not form.is_valid():
            return TemplateResponse(
                request, self.template_name, self._context(problem, task, form), status=400
            )
        data = form.cleaned_data
        fields = {
            "mode": data["mode"],
            "language": data["language"],
            "time_limit_seconds": data["time_limit_seconds"],
            "memory_limit_mb": data["memory_limit_mb"],
            "results_visibility": data["results_visibility"],
            "visible_tests": data["visible_tests"],
            "hidden_tests": data["hidden_tests"],
        }
        if data.get("starter_file") is not None:
            fields["starter_notebook"] = data["starter_file"]
        elif data.get("clear_starter"):
            fields["starter_notebook"] = None
        if data.get("reference_file") is not None:
            fields["reference_notebook"] = data["reference_file"]
        try:
            saved = services.save_task(problem, actor=request.user, request=request, **fields)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return TemplateResponse(
                request, self.template_name, self._context(problem, task, form), status=400
            )
        stale = NotebookRun.objects.filter(task=saved, is_reference=False, status=RunStatus.DONE).exclude(
            tests_hash=services.tests_hash(saved)
        )
        if stale.exists():
            messages.warning(
                request,
                _(
                    "Testy się zmieniły – wyniki już sprawdzonych prac są nieaktualne. "
                    "Użyj „Przelicz wszystko”."
                ),
            )
        messages.success(request, _("Zapisano ustawienia notatnika."))
        return redirect("web:coordinator-notebook-task", pk=problem.pk)


def _notebook_response(notebook: dict, filename: str, *, attachment: bool) -> HttpResponse:
    response = HttpResponse(
        json.dumps(notebook, ensure_ascii=False, indent=1),
        content_type="application/x-ipynb+json; charset=utf-8",
    )
    disposition = "attachment" if attachment else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class NotebookStarterPreviewView(NotebookCoordinatorMixin, View):
    """Notatnik startowy dokładnie taki, jaki dostaje uczestnik (z testami widocznymi)."""

    def get(self, request, pk: int):
        problem = self.problem(pk)
        task = services.task_for(problem)
        if task is None:
            raise Http404
        return _notebook_response(
            services.starter_notebook(task), services.starter_filename(task), attachment=True
        )


class NotebookResultsView(NotebookCoordinatorMixin, ThrottledFormMixin, View):
    template_name = "notebooks/coordinator_results.html"
    throttle_scope = "notebooks"

    def get(self, request, pk: int):
        problem = self.problem(pk)
        task = services.task_for(problem)
        if task is None:
            raise Http404
        if request.GET.get("format") == "csv":
            response = HttpResponse(services.results_csv(task), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = (
                f'attachment; filename="notatnik-{problem.stage_id}-{problem.number}.csv"'
            )
            return response
        runs = services.latest_runs(task)
        current_hash = services.tests_hash(task)
        tests = grader.validate_tests(task.hidden_tests) or grader.validate_tests(task.visible_tests)
        rows = []
        for run in runs:
            by_id = {item["id"]: item for item in (run.results or run.visible_results or [])}
            rows.append(
                {
                    "run": run,
                    "cells": [by_id.get(test["id"]) for test in tests],
                    "stale": run.status == RunStatus.DONE and run.tests_hash != current_hash,
                    "error": services.error_text(run.error_code) if run.error_code else "",
                }
            )
        return TemplateResponse(
            request, self.template_name, {"problem": problem, "task": task, "tests": tests, "rows": rows}
        )

    def post(self, request, pk: int):
        problem = self.problem(pk)
        task = services.task_for(problem)
        if task is None:
            raise Http404
        try:
            count = services.rerun_all(task, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Do ponownego sprawdzenia: %(count)s prac.") % {"count": count})
        return redirect("web:coordinator-notebook-results", pk=problem.pk)


class NotebookRunDetailView(NotebookCoordinatorMixin, View):
    template_name = "notebooks/coordinator_run.html"

    def get(self, request, pk: int):
        run = get_object_or_404(
            NotebookRun.objects.for_competition(self.competition).select_related(
                "task__problem__stage", "submission__entry__participant"
            ),
            pk=pk,
        )
        return TemplateResponse(
            request,
            self.template_name,
            {
                "run": run,
                "problem": run.task.problem,
                "error": services.error_text(run.error_code) if run.error_code else "",
            },
        )


# --- uczestnik ------------------------------------------------------------------------------------


class _ParticipantNotebookMixin(ParticipantRequiredMixin):
    def task(self, pk: int) -> NotebookTask:
        if not services.is_enabled(self.competition):
            raise Http404
        problem = get_object_or_404(
            Problem.objects.for_competition(self.competition).select_related(
                "stage", "stage__edition__competition"
            ),
            pk=pk,
        )
        try:
            return services.participant_task(problem, self.participant)
        except DomainError as exc:
            raise Http404 from exc


class ParticipantLabView(_ParticipantNotebookMixin, View):
    """Strona zadania z laboratorium: instrukcja i przycisk otwierający JupyterLite w **osobnej karcie**.

    Bez ramki: dokument laboratorium wykonuje kod ucznia, a w ramce miałby dostęp do strony serwisu
    przez ``window.parent`` (ten sam origin). W osobnej karcie (``rel="noopener"``) nie ma do niej
    żadnego odwołania, a nagłówki izolacji okien laboratorium odcinają też okna, które sam otworzy
    (QC-01 § 3.5). Polityka CSP tej strony jest zwykłą polityką serwisu.
    """

    template_name = "notebooks/participant_lab.html"

    def get(self, request, pk: int):
        task = self.task(pk)
        problem = task.problem
        if services.request_has_staff_role(request):
            # Konto z rolą personelu nie dostaje laboratorium (wykonanie kodu z jego sesją) – tylko
            # podgląd notatnika startowego (``services.has_staff_role``, QC-01 § 3.5).
            return _readonly_response(
                request,
                services.starter_notebook(task),
                title=_("Notatnik startowy – podgląd"),
                problem=problem,
                staff_notice=True,
            )
        starter_url = services.starter_url(task, self.participant, request.user)
        # Osobny host laboratorium (QC-02): adresy bezwzględne na ten host – także pobranie notatnika
        # startowego, bo hosty serwisu ``/notebook-starter/`` już nie podają. Bez niego – względne (QC-01).
        origin = lab.lab_origin(request)
        return TemplateResponse(
            request,
            self.template_name,
            {
                "task": task,
                "problem": problem,
                "lab_url": lab.lab_url(starter_url, origin),
                "starter_url": f"{origin}{starter_url}",
                "transfer_mb": lab.transfer_megabytes(),
                "visible_count": len(task.visible_tests or []),
            },
        )


class ParticipantStarterView(View):
    """Notatnik startowy dla JupyterLite (``fromURL``) i do pobrania. Bez testów ukrytych.

    Adres bez prefiksu konkursu (``/notebook-starter/<token>/<plik>``), bo polityka CSP laboratorium
    jest napisem stałym (Caddy) i wymienia tę jedną ścieżkę. Konkurs, zadanie i konto niesie
    podpisany token z terminem ważności; konto musi się zgadzać z zalogowanym, a bramki uczestnika
    (flaga, wpis na etap, etap otwarty) sprawdza ten sam serwis, co strona laboratorium.
    """

    def get(self, request, token: str, filename: str):
        # Przy osobnym hoście laboratorium (QC-02) notatnik startowy podaje wyłącznie
        # ``lab_host_starter``; tu dochodzi tylko w razie pominięcia ``NotebookLabHostMiddleware``.
        if lab.lab_host() or not request.user.is_authenticated or services.request_has_staff_role(request):
            raise Http404
        task = services.task_from_starter_token(token, request.user)
        if task is None or filename != services.starter_filename(task):
            raise Http404
        # Nadzór zdalny (PROC-01): adres nie niesie zadania, więc bramka middleware go nie rozpozna –
        # token zdobyty przy gotowej sesji nie może oddawać treści zadania po jej zerwaniu.
        from apps.proctoring.services import gate_decision

        stage = task.problem.stage
        if gate_decision(request.user, stage, stage.edition.competition) is not None:
            raise PermissionDenied
        attachment = request.GET.get("download") == "1"
        return _notebook_response(services.starter_notebook(task), filename, attachment=attachment)


#: Polityka odpowiedzi z notatnikiem startowym na hoście laboratorium: dokument JSON nie ma niczego
#: wykonywać ani się osadzać, nawet otwarty wprost w karcie.
LAB_HOST_STARTER_POLICY = "default-src 'none'; frame-ancestors 'none'; sandbox"


def lab_host_starter(request, token: str, filename: str) -> HttpResponse:
    """Notatnik startowy na **osobnym hoście laboratorium** (QC-02 § 6) – bez sesji serwisu.

    Woła go bezpośrednio ``NotebookLabHostMiddleware`` (bez urlconfu i bez warstw sesji, konkursu
    i CSRF – host laboratorium nie dostaje ciasteczek serwisu). Jedynym poświadczeniem jest
    podpisany token z krótkim terminem; konto z tokenu przechodzi te same bramki co w QC-01
    (uczestnik etapu, bez roli personelu, nadzór zdalny) – na stanie z chwili pobrania.
    """
    from django.http import HttpResponseNotAllowed, HttpResponseNotFound

    from apps.proctoring.services import gate_decision

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    target = services.lab_host_starter_target(token)
    if target is None or filename != services.starter_filename(target[0]):
        return HttpResponseNotFound("Not found.", content_type="text/plain")
    task, user = target
    stage = task.problem.stage
    if gate_decision(user, stage, stage.edition.competition) is not None:
        return HttpResponse("Forbidden.", status=403, content_type="text/plain")
    attachment = request.GET.get("download") == "1"
    response = _notebook_response(services.starter_notebook(task), filename, attachment=attachment)
    response["Content-Security-Policy"] = LAB_HOST_STARTER_POLICY
    return response


# --- podgląd tylko do odczytu (personel) -----------------------------------------------------------


READONLY_TEMPLATE = "notebooks/readonly.html"


def _readonly_response(request, notebook: dict, *, title: str, problem=None, staff_notice: bool = False):
    return TemplateResponse(
        request,
        READONLY_TEMPLATE,
        {
            "cells": render.render_cells(notebook),
            "title": title,
            "problem": problem,
            "staff_notice": staff_notice,
        },
    )


class SubmissionNotebookView(LoginRequiredMixin, View):
    """Praca ``.ipynb`` w podglądzie tylko do odczytu – dla każdego, kto może pobrać tę pracę.

    Widoczność jest ta sama, co przy pobraniu pliku (``Submission.objects.for_user``: koordynator
    konkursu, recenzent z przydziałem, komisja przy reklamacji, autor). Nic się nie wykonuje, wyjścia
    HTML/JS są pomijane (``apps.notebooks.render``) – to jest droga personelu do notatników zamiast
    laboratorium.
    """

    def get(self, request, pk: int):
        from apps.submissions.models import Submission

        competition = getattr(request, "competition", None)
        if not services.is_enabled(competition):
            raise Http404
        submission = get_object_or_404(
            Submission.objects.for_user(request.user, competition).select_related("problem"), pk=pk
        )
        try:
            notebook = services.read_submission_notebook(submission.latest_file)
        except notebook_io.NotebookError as exc:
            raise Http404 from exc
        except Exception as exc:  # noqa: BLE001 - storage rzuca własnymi wyjątkami
            raise Http404 from exc
        title = _("Praca – podgląd notatnika (wersja %(version)s)") % {"version": submission.version}
        return _readonly_response(request, notebook, title=title, problem=submission.problem)


class TaskNotebookView(NotebookCoordinatorMixin, View):
    """Notatnik startowy (jak u uczestnika) albo wzorcowy w podglądzie tylko do odczytu."""

    def get(self, request, pk: int, kind: str):
        problem = self.problem(pk)
        task = services.task_for(problem)
        if task is None:
            raise Http404
        if kind == "starter":
            notebook, title = services.starter_notebook(task), _("Notatnik startowy – podgląd")
        elif kind == "reference" and task.reference_notebook:
            notebook, title = task.reference_notebook, _("Notatnik wzorcowy – podgląd")
        else:
            raise Http404
        return _readonly_response(request, notebook, title=title, problem=problem)
