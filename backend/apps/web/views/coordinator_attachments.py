"""Załączniki zadania w panelu koordynatora (PUB-02, ``docs/tasks/PUB-02.md``).

Trzy akcje POST przy formularzu zadania (``web:coordinator-problem-edit``, sekcja „Załączniki”):
dodanie pliku, zmiana opisu/kolejności, usunięcie. Każda wraca na stronę zadania z komunikatem.
Rolę sprawdza mixin **i** serwis (``apps.competitions.attachments.require_coordinator``).
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import View

from apps.competitions import attachments as service
from apps.competitions.models import Problem, ProblemAttachment
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin


def _back(problem_id: int):
    return redirect(reverse("web:coordinator-problem-edit", args=[problem_id]) + "#zalaczniki")


class ProblemAttachmentAddView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/problems/<id>/attachments/`` – wgranie załącznika (pole ``file``)."""

    def post(self, request, pk: int):
        problem = get_object_or_404(Problem.objects.for_competition(request.competition), pk=pk)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            messages.error(request, "Wybierz plik załącznika.")
            return _back(problem.pk)
        try:
            attachment = service.add_attachment(
                problem,
                uploaded,
                actor=request.user,
                competition=request.competition,
                description=request.POST.get("description", ""),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _back(problem.pk)
        messages.success(request, f"Dodano załącznik {attachment.filename}.")
        return _back(problem.pk)


def _attachment(request, pk: int) -> ProblemAttachment:
    return get_object_or_404(
        ProblemAttachment.objects.for_competition(request.competition).select_related("problem"), pk=pk
    )


class ProblemAttachmentUpdateView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/problem-attachments/<id>/`` – opis i kolejność."""

    def post(self, request, pk: int):
        attachment = _attachment(request, pk)
        raw_position = (request.POST.get("position") or "").strip()
        try:
            position = int(raw_position) if raw_position else None
        except ValueError:
            messages.error(request, "Kolejność musi być liczbą całkowitą.")
            return _back(attachment.problem_id)
        try:
            service.update_attachment(
                attachment,
                actor=request.user,
                competition=request.competition,
                description=request.POST.get("description"),
                position=position,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _back(attachment.problem_id)
        messages.success(request, f"Zapisano załącznik {attachment.filename}.")
        return _back(attachment.problem_id)


class ProblemAttachmentDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/problem-attachments/<id>/delete/`` – usunięcie wiersza i pliku."""

    def post(self, request, pk: int):
        attachment = _attachment(request, pk)
        problem_id, name = attachment.problem_id, attachment.filename
        try:
            service.delete_attachment(
                attachment, actor=request.user, competition=request.competition, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _back(problem_id)
        messages.success(request, f"Usunięto załącznik {name}.")
        return _back(problem_id)
