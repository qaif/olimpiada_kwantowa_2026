"""Wstawki notatników do cudzych ekranów: karta zadania uczestnika i panel oceny recenzenta.

Tagi, a nie dopisywanie kontekstu w widokach ``apps.web``: szablony innych aplikacji dostają jedną
linijkę, a cała logika (flaga, rola, widoczność) zostaje tutaj. Przy wyłączonej fladze tag nie
wykonuje **ani jednego** zapytania – budżety zapytań paneli (``apps/tenancy/tests``) zostają.
"""

from __future__ import annotations

from django import template

from .. import lab, services

register = template.Library()


@register.inclusion_tag("notebooks/_problem_card_panel.html", takes_context=True)
def notebook_card_panel(context, row, stage=None):
    request = context.get("request")
    competition = getattr(request, "competition", None)
    empty = {"show": False}
    if not services.is_enabled(competition):
        return empty
    problem = row.get("problem") if isinstance(row, dict) else getattr(row, "problem", None)
    task = services.task_for(problem) if problem is not None else None
    if task is None:
        return empty
    versions = row.get("versions") if isinstance(row, dict) else None
    latest = versions[0] if versions else None
    run = services.participant_results(task, latest) if latest is not None else None
    return {
        "show": True,
        "task": task,
        "problem": problem,
        # Konto z rolą personelu nie dostaje laboratorium (``services.has_staff_role``).
        "staff": services.request_has_staff_role(request),
        "opened": problem.stage.has_opened(),
        "run": run,
        "transfer_mb": lab.transfer_megabytes(),
    }


@register.inclusion_tag("notebooks/_review_panel.html", takes_context=True)
def notebook_review_panel(context, submission):
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if submission is None or not services.is_enabled(competition):
        return {"show": False}
    task = services.task_for(submission.problem)
    if task is None or not task.is_autograded:
        return {"show": False}
    run = services.run_for_submission(submission)
    return {
        "show": True,
        "task": task,
        "submission": submission,
        "run": run,
        "error": services.error_text(run.error_code) if run is not None and run.error_code else "",
    }
