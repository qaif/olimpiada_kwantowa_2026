"""Znacznik szkieletu panelu koordynatora: menu boczne.

Menu jest znacznikiem, a nie procesorem kontekstu, bo dotyczy **jednej** gałęzi serwisu: procesor
liczyłby etapy i liczniki także przy renderowaniu strony głównej i każdego fragmentu HTMX.
Znacznik woła się dokładnie raz – w ``web/coordinator/base.html`` – i to jedyne miejsce, z którego
ekrany panelu dowiadują się o istnieniu menu.

Osobna biblioteka od ``coordinator_extras``: tamta zbiera drobne pomocniki wołane z wnętrza
ekranów, ta odpowiada za ramę, w której te ekrany stoją.
"""

from __future__ import annotations

from django import template

from apps.web.coordinator_nav import navigation

register = template.Library()


@register.inclusion_tag("web/coordinator/_nav.html", takes_context=True)
def coordinator_nav(context):
    """Menu boczne panelu: sekcje, pozycja aktywna, liczniki i pole wyszukiwania."""
    request = context["request"]
    data = navigation(request)
    data["request"] = request
    return data
