"""Ekrany notatników kwantowych: panel koordynatora i laboratorium uczestnika (QC-01 § 7).

Kolejność bramek jak w ocenie AI: najpierw rola (mixin – 403 dla innej roli), potem flaga konkursu
(404). Obiekty zawsze przez ``for_competition`` – zadanie z sąsiedniego konkursu daje 404.
"""

from __future__ import annotations

import json

from django.contrib import messages
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

from . import lab, services
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
    """Strona zadania z osadzonym JupyterLite. Jedyna strona serwisu z ``frame-src 'self'`` (§ 3.2)."""

    template_name = "notebooks/participant_lab.html"

    def get(self, request, pk: int):
        task = self.task(pk)
        problem = task.problem
        filename = services.starter_filename(task)
        starter_path = reverse("web:participant-notebook-starter", args=[problem.pk, filename])
        response = TemplateResponse(
            request,
            self.template_name,
            {
                "task": task,
                "problem": problem,
                "lab_url": lab.lab_url(starter_path),
                "starter_url": starter_path,
                "transfer_mb": lab.transfer_megabytes(),
                "visible_count": len(task.visible_tests or []),
            },
        )
        # Polityka składa się w middleware (po renderowaniu – motyw dokłada tam swoje źródła);
        # tu wyłącznie zgoda na ramkę z własnej domeny dla tej jednej strony.
        request._csp_extra_frame_sources = ("'self'",)
        return response


class ParticipantStarterView(_ParticipantNotebookMixin, View):
    """Notatnik startowy dla JupyterLite (``fromURL``) i do pobrania. Bez testów ukrytych."""

    def get(self, request, pk: int, filename: str):
        task = self.task(pk)
        if filename != services.starter_filename(task):
            raise Http404
        attachment = request.GET.get("download") == "1"
        return _notebook_response(services.starter_notebook(task), filename, attachment=attachment)
