"""Zarządzanie etapami i zadaniami z panelu koordynatora.

Osobny moduł od ``coordinator.py``, bo to inny rodzaj ekranu: tam są akcje (POST → serwis →
komunikat → powrót na pulpit), tutaj pełne formularze z własnymi stronami, walidacją pod polami
i uploadem pliku. Wspólne zostają uprawnienia (``CoordinatorRequiredMixin``) i zasada, że reguła
domenowa mieszka w serwisie (``apps.competitions.services``), a widok wyłącznie orkiestruje.

Dlaczego terminy w ogóle wychodzą z ``/admin/``: kalendarz edycji jest **codzienną** pracą
koordynatora, a nie czynnością administratora bazy. Panel dokłada do surowego formularza admina
trzy rzeczy, których tam nie ma i mieć nie może: godziny w czasie polskim zamiast UTC, blokady
zależne od stanu zawodów (zgłoszenia, zamknięcie etapu) oraz wpis audytowy z różnicą pól, czytelny
razem z resztą historii etapu. Skala punktacji i próg kwalifikacji zostają w ``/admin/`` – to
konfiguracja oceniania, którą rusza się raz na edycję.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.competitions.models import Problem, Stage
from apps.competitions.services import (
    create_problem,
    create_stage,
    current_edition,
    delete_problem,
    missing_stage_kinds,
    stage_has_submissions,
    update_problem,
    update_stage,
)
from apps.core.api import DomainError
from apps.web.forms import ProblemForm, StageCreateForm, StageForm
from apps.web.mixins import CoordinatorRequiredMixin

#: Pola zadania, które widok przekazuje do serwisu. Plik i potwierdzenie idą osobno.
PROBLEM_FIELDS = ("number", "title", "allowed_formats", "max_file_mb")

PROBLEMS_TEMPLATE = "web/coordinator/problems.html"


def _stage_for_edit(stage_id: int) -> Stage:
    return get_object_or_404(Stage.objects.select_related("edition"), pk=stage_id)


def _problem_counts(stage: Stage) -> dict[int, int]:
    """Liczba rozwiązań per zadanie – jedno zapytanie na całą listę, nie jedno na wiersz."""
    from django.db.models import Count

    from apps.submissions.models import Submission

    rows = Submission.objects.filter(problem__stage=stage).values("problem_id").annotate(total=Count("id"))
    return {row["problem_id"]: row["total"] for row in rows}


def render_problem_list(request, stage: Stage, form: ProblemForm, *, status: int = 200):
    """Strona listy zadań etapu. Wspólna dla dodawania i dla nieudanego usunięcia."""
    counts = _problem_counts(stage)
    context = {
        "stage": stage,
        "form": form,
        "now": timezone.now(),
        "rows": [
            {"problem": problem, "submissions": counts.get(problem.pk, 0)}
            for problem in stage.problems.order_by("number", "id")
        ],
    }
    return TemplateResponse(request, PROBLEMS_TEMPLATE, context, status=status)


class StageEditView(CoordinatorRequiredMixin, View):
    """``/coordinator/stages/<id>/edit/`` – oś czasu etapu.

    Odmowa serwisu (etap zamknięty, cofnięty deadline przy zgłoszeniach) wraca **z kodem błędu
    domenowego**, a nie jako 302 z komunikatem: to samo żądanie wysłane skryptem ma dostać 409,
    a nie „przekierowanie na pulpit”, po którym nie widać, czy zmiana weszła.
    """

    template_name = "web/coordinator/stage_form.html"

    def get(self, request, stage_id: int):
        stage = _stage_for_edit(stage_id)
        return self._render(request, stage, StageForm(instance=stage))

    def post(self, request, stage_id: int):
        stage = _stage_for_edit(stage_id)
        form = StageForm(request.POST, instance=stage)
        if not form.is_valid():
            return self._render(request, stage, form, status=400)
        try:
            update_stage(stage, request.user, request=request, **form.changed_values())
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            # Świeży obiekt z bazy: ``ModelForm`` zdążył już wpisać odrzucone wartości do
            # ``form.instance``, a strona ma pokazać stan, który faktycznie obowiązuje.
            stage = _stage_for_edit(stage_id)
            return self._render(request, stage, StageForm(instance=stage), status=exc.status_code)
        messages.success(request, f"Terminy etapu {stage.get_kind_display()} zostały zapisane.")
        return redirect(reverse("web:coordinator"))

    def _render(self, request, stage: Stage, form: StageForm, *, status: int = 200):
        context = {
            "stage": stage,
            "form": form,
            "now": timezone.now(),
            "problem_count": stage.problems.count(),
            "has_submissions": stage_has_submissions(stage),
        }
        return TemplateResponse(request, self.template_name, context, status=status)


class StageCreateView(CoordinatorRequiredMixin, View):
    """``/coordinator/stages/new/`` – dodanie etapu bieżącej edycji.

    Etap powstaje przez ``create_stage``, więc od razu ma domyślną skalę punktacji (0/2/5/6)
    i próg kwalifikacji. Etap bez nich byłby niemożliwy do oceniania: recenzent nie miałby czego
    wybrać, a przeliczenie wyników przerywałoby się na braku reguły.
    """

    template_name = "web/coordinator/stage_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.edition = current_edition()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        if self.edition is None:
            return self._no_edition(request)
        return self._render(request, StageCreateForm(kind_choices=missing_stage_kinds(self.edition)))

    def post(self, request):
        if self.edition is None:
            return self._no_edition(request)
        choices = missing_stage_kinds(self.edition)
        form = StageCreateForm(request.POST, kind_choices=choices)
        if not form.is_valid():
            return self._render(request, form, status=400)
        data = dict(form.cleaned_data)
        try:
            stage = create_stage(edition=self.edition, **data)
        except (ValidationError, IntegrityError):
            # Wyścig o ostatni wolny rodzaj etapu: para (edycja, rodzaj) jest unikalna w bazie.
            form.add_error("kind", "Ten rodzaj etapu istnieje już w bieżącej edycji.")
            return self._render(request, form, status=409)
        messages.success(
            request,
            f"Etap {stage.get_kind_display()} został dodany razem z domyślną skalą i progiem kwalifikacji.",
        )
        return redirect(reverse("web:coordinator"))

    def _no_edition(self, request):
        messages.error(request, "Nie ustawiono bieżącej edycji – etap nie ma do czego należeć.")
        return redirect(reverse("web:coordinator"))

    def _render(self, request, form, *, status: int = 200):
        context = {"stage": None, "form": form, "edition": self.edition, "now": timezone.now()}
        return TemplateResponse(request, self.template_name, context, status=status)


class StageProblemsView(CoordinatorRequiredMixin, View):
    """``/coordinator/stages/<id>/problems/`` – lista zadań etapu i formularz dodania."""

    def get(self, request, stage_id: int):
        stage = _stage_for_edit(stage_id)
        return render_problem_list(request, stage, ProblemForm(stage=stage))

    def post(self, request, stage_id: int):
        stage = _stage_for_edit(stage_id)
        form = ProblemForm(request.POST, request.FILES, stage=stage)
        if not form.is_valid():
            return render_problem_list(request, stage, form, status=400)
        data = {name: form.cleaned_data[name] for name in PROBLEM_FIELDS}
        try:
            problem = create_problem(
                stage=stage,
                actor=request.user,
                statement=form.uploaded_statement(),
                request=request,
                **data,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return render_problem_list(request, stage, form, status=exc.status_code)
        messages.success(request, f"Dodano zadanie {problem.number}: {problem.title}.")
        return redirect(reverse("web:coordinator-stage-problems", args=[stage.pk]))


class ProblemEditView(CoordinatorRequiredMixin, View):
    """``/coordinator/problems/<id>/edit/`` – zmiana zadania, w tym podmiana treści PDF."""

    template_name = "web/coordinator/problem_form.html"

    def get(self, request, pk: int):
        problem = self._problem(pk)
        return self._render(request, problem, ProblemForm(instance=problem, stage=problem.stage))

    def post(self, request, pk: int):
        problem = self._problem(pk)
        form = ProblemForm(request.POST, request.FILES, instance=problem, stage=problem.stage)
        if not form.is_valid():
            return self._render(request, problem, form, status=400)
        data = {name: form.cleaned_data[name] for name in PROBLEM_FIELDS}
        try:
            update_problem(
                problem,
                request.user,
                statement=form.uploaded_statement(),
                confirm_open_stage=bool(form.cleaned_data.get("confirm_open_stage")),
                request=request,
                **data,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            problem = self._problem(pk)
            return self._render(request, problem, form, status=exc.status_code)
        messages.success(request, f"Zapisano zadanie {data['number']}.")
        return redirect(reverse("web:coordinator-stage-problems", args=[problem.stage_id]))

    def _problem(self, pk: int) -> Problem:
        return get_object_or_404(Problem.objects.select_related("stage", "stage__edition"), pk=pk)

    def _render(self, request, problem: Problem, form: ProblemForm, *, status: int = 200):
        context = {
            "problem": problem,
            "stage": problem.stage,
            "form": form,
            "now": timezone.now(),
            "stage_has_opened": problem.stage.has_opened(),
        }
        return TemplateResponse(request, self.template_name, context, status=status)


class ProblemDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/problems/<id>/delete/`` – usunięcie zadania bez rozwiązań.

    Odmowa (są rozwiązania) renderuje listę zadań z kodem 409 zamiast przekierowywać: przy 302
    kod odmowy ginie w przekierowaniu, a wywołujący nie odróżnia „nie usunięto” od „usunięto”.
    """

    def post(self, request, pk: int):
        problem = get_object_or_404(Problem.objects.select_related("stage"), pk=pk)
        stage = problem.stage
        try:
            delete_problem(problem, request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return render_problem_list(request, stage, ProblemForm(stage=stage), status=exc.status_code)
        messages.success(request, f"Zadanie {problem.number} zostało usunięte.")
        return redirect(reverse("web:coordinator-stage-problems", args=[stage.pk]))
