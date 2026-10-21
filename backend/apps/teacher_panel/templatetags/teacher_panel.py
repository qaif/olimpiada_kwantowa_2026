"""Znacznik menu panelu nauczyciela (``teacher_nav``) – odpowiednik ``participant_nav`` (UX-PANEL-02 D1).

Znacznik, a nie kontekst dopisywany w każdym widoku: ramę dostają ekrany z trzech aplikacji
(``apps.web`` – konta uczniów i import, ``apps.school_stats`` – statystyki, ta aplikacja – Start,
postęp, szkoła), a każdy z nich ma już swój widok i swoje testy.
"""

from __future__ import annotations

from django import template

from ..nav import navigation

register = template.Library()


@register.inclusion_tag("teacher_panel/_nav.html", takes_context=True)
def teacher_nav(context):
    data = navigation(context)
    data["request"] = context.get("request")
    return data
