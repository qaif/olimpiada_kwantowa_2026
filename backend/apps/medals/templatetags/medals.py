"""Znaczniki szablonów medali: odznaka nagrody i odnośnik ze strony wyników (MED-01)."""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from apps.medals.models import Award

register = template.Library()

#: Klasa CSS odznaki każdej nagrody (``static/medals/medals.css``).
AWARD_CLASSES = {
    Award.GOLD: "badge medal medal--gold",
    Award.SILVER: "badge medal medal--silver",
    Award.BRONZE: "badge medal medal--bronze",
    Award.HONOURABLE: "badge medal medal--hm",
}


@register.filter
def award_label(value) -> str:
    """Etykieta nagrody w języku strony („złoty medal”); ``NONE`` – pusty napis."""
    if not value or value == Award.NONE:
        return ""
    return str(dict(Award.choices).get(value, value))


@register.filter
def award_class(value) -> str:
    return AWARD_CLASSES.get(value, "")


@register.simple_tag(takes_context=True)
def medal_links(context, stage) -> str:
    """Odnośniki do medali i rankingu krajów – **wyłącznie** gdy medale etapu są ogłoszone.

    Pusty napis w każdym innym przypadku, także w konkursie bez flagi ``medals`` (bez zapytania):
    strona wyników Olimpiady Kwantowej wychodzi co do bajtu taka, jak przed MED-01.
    """
    from apps.medals.services import frozen_scheme_for_results

    competition = getattr(context.get("request"), "competition", None)
    if stage is None or frozen_scheme_for_results(stage, competition) is None:
        return ""
    return mark_safe(  # noqa: S308 - szablon aplikacji, wartości przez autoescape szablonu
        render_to_string("medals/_results_links.html", {"stage": stage}, request=context.get("request"))
    )
