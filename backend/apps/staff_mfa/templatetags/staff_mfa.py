"""Baner okresu przejściowego 2FA – ``{% two_factor_grace_banner %}`` w ``templates/base.html``.

Znacznik, a nie procesor kontekstu: procesor liczyłby się na każdej stronie każdego serwisu,
a ten znacznik czyta jeden atrybut żądania, który ustawia warstwa wymuszająca
(``request.two_factor_grace_until``) – i wyłącznie wtedy rysuje cokolwiek. Bez atrybutu (funkcja
wyłączona, konto bez wymogu, anonim) oddaje pusty napis, więc strona jest bajt w bajt taka jak dotąd.
Stoi w ``base.html`` poza slotami motywu, więc motyw (IQO) nie może go zasłonić ani pominąć.
"""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string

register = template.Library()


@register.simple_tag(takes_context=True)
def two_factor_grace_banner(context) -> str:
    request = context.get("request")
    deadline = getattr(request, "two_factor_grace_until", None) if request is not None else None
    if deadline is None:
        return ""
    # Na samym ekranie konfiguracji baner powtarzałby to, co strona mówi w pierwszym zdaniu.
    if request.resolver_match is not None and request.resolver_match.url_name == "twofactor-setup":
        return ""
    return render_to_string("staff_mfa/_grace_banner.html", {"deadline": deadline}, request=request)
