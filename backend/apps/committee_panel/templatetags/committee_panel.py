"""Znacznik ramy panelu komisji: menu (``committee_nav``).

Znacznik, a nie kontekst dopisywany w każdym widoku: ramę dostają ekrany z kilku aplikacji (kolejka
recenzenta, kolejka reklamacji, pokoje wideo, profil konta, Start, materiały, powiadomienia), a każdy
z nich ma już swój widok i swoje testy. Szablon dziedziczy ``committee_panel/base.html`` i ma menu –
bez zmiany widoku.
"""

from __future__ import annotations

from django import template

from ..nav import navigation

register = template.Library()


@register.inclusion_tag("committee_panel/_nav.html", takes_context=True)
def committee_nav(context):
    data = navigation(context)
    data["request"] = context.get("request")
    return data
