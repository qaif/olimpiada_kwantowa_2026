"""Wstawki edytora LaTeX do cudzych ekranów: karta zadania uczestnika i „Terminy etapu” koordynatora.

Jak ``apps.notebooks.templatetags.notebook_tags``: szablony innych aplikacji dostają jedną linijkę,
a cała logika (flaga, rola, widoczność) zostaje tutaj. Przy wyłączonej fladze (albo bez klucza
integracji) tag nie wykonuje **ani jednego** zapytania i nic nie rysuje – karta zadania jest co do
bajtu taka jak bez tej aplikacji.
"""

from __future__ import annotations

from django import template
from django.urls import reverse

from .. import services
from ..forms import LatexSubmitForm

register = template.Library()


@register.inclusion_tag("webtex/_problem_card_panel.html", takes_context=True)
def latex_card_panel(context, row, stage=None):
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if stage is None or not services.available(competition):
        return {"show": False}
    problem = row.get("problem") if isinstance(row, dict) else getattr(row, "problem", None)
    if problem is None or stage.is_interview:
        return {"show": False}
    from apps.notebooks.services import request_has_staff_role

    return {
        "show": True,
        "problem": problem,
        # Konto z rolą personelu nie dostaje edytora (TEX-01 D7) – tylko wyjaśnienie.
        "staff": request_has_staff_role(request),
        "open_url": reverse("web:problem-latex", args=[stage.pk, problem.number]),
        "submit_url": reverse("web:problem-latex-submit", args=[stage.pk, problem.number]),
        # Formularz złożenia – tylko gdy etap przyjmuje rozwiązania, a zadanie dopuszcza PDF.
        "can_submit": bool(context.get("upload_open")) and "pdf" in (problem.allowed_formats or []),
        "form": context.get("latex_form") or LatexSubmitForm(),
        "error": context.get("latex_error") or "",
        "issues": context.get("latex_issues") or [],
        "notice": context.get("latex_notice") or "",
        "csrf_token": context.get("csrf_token"),
    }


@register.inclusion_tag("webtex/_stage_settings_link.html", takes_context=True)
def latex_stage_settings_link(context, stage):
    """Wiersz „Edytor LaTeX” na ekranie „Terminy etapu” – przy wyłączonej fladze pusty, bez zapytań."""
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if stage is None or stage.is_interview or not services.is_enabled(competition):
        return {"show": False}
    return {"show": True, "stage": stage, "options": services.stage_options(stage)}
