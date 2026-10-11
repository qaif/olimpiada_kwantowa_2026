"""Znaczniki szablonów sieci absolwentów.

``alumni_nav`` rozstrzyga, czy w pasku konta stoi „Absolwenci”. Znacznik, a nie procesor kontekstu,
bo procesory kontekstu są wspólnym plikiem, który zmienia równolegle wiele zadań – a pytanie jest
tanie: flaga to pole wiersza konkursu w pamięci (**zero zapytań**), a rola przychodzi już policzona
z procesora ``roles`` (``is_participant``).
"""

from __future__ import annotations

from django import template

from apps.alumni.models import enabled

register = template.Library()


@register.simple_tag(takes_context=True)
def alumni_nav(context) -> bool:
    request = context.get("request")
    competition = getattr(request, "competition", None)
    return bool(context.get("is_participant")) and enabled(competition)
