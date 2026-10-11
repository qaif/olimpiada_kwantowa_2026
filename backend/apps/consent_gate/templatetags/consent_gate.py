"""Kafelek „Uczestnicy z brakującymi zgodami” na pulpicie koordynatora (CONS-01 § 5).

Znacznik, a nie klucz w ``dashboard_context``: pulpit składa wiele zadań naraz, a jedna linijka
w szablonie jest mniejszym punktem styku niż kolejna pozycja w kontekście widoku.
"""

from __future__ import annotations

from django import template

from apps.consent_gate import report
from apps.consent_gate.middleware import enabled

register = template.Library()


@register.inclusion_tag("consent_gate/_coordinator_tile.html", takes_context=True)
def consent_gap_tile(context) -> dict:
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if competition is None:
        return {"show": False}
    return {"show": True, "count": report.count(competition), "gate_enabled": enabled()}
