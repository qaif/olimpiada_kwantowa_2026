"""Znaczniki szablonów statystyk szkół.

``school_stats_link`` stoi na pulpicie opiekuna (``web/supervisor/dashboard.html``) jako jedna
linijka: pulpit nie musi wiedzieć o fladze ani o adresie, a przy wyłączonej fladze znacznik nie
wypisuje niczego – strona jest wtedy co do bajtu taka jak przed STAT-01. Odczyt flagi nie dotyka
bazy (to pole wiersza konkursu, który ``CompetitionMiddleware`` ma już w pamięci).
"""

from __future__ import annotations

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _

from apps.school_stats.services import enabled

register = template.Library()


@register.simple_tag(takes_context=True)
def school_stats_link(context) -> str:
    request = context.get("request")
    if request is None or not enabled(getattr(request, "competition", None)):
        return ""
    return format_html(
        '<a class="btn btn--secondary" href="{}">{}</a>',
        reverse("web:supervisor-statistics"),
        _("Statystyki uczniów i szkoły"),
    )
