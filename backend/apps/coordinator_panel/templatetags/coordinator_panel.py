"""Znaczniki ramy panelu koordynatora (UX-PANEL-04): okruszki i przycisk „Przypnij”.

Znaczniki, a nie kontekst widoków: ramę ``web/coordinator/base.html`` dziedziczy ponad sto ekranów z wielu
aplikacji, a każdy z nich ma swój widok i swoje testy. Dane bierze się z menu policzonego raz na żądanie
(``apps.coordinator_panel.shell.navigation``) – ten sam obiekt, który rysuje kolumnę menu.
"""

from __future__ import annotations

from django import template

from .. import shell

register = template.Library()


@register.inclusion_tag("coordinator_panel/_crumbs.html", takes_context=True)
def coordinator_crumbs(context):
    request = context.get("request")
    if request is None:
        return {"trail": []}
    return {"trail": shell.navigation(request)["nav_trail"]}


@register.inclusion_tag("coordinator_panel/_pin_button.html", takes_context=True)
def coordinator_pin_button(context):
    request = context.get("request")
    if request is None:
        return {"pin": None}
    return {"pin": shell.current_pin(request), "request": request, "csrf_token": context.get("csrf_token")}


@register.simple_tag(takes_context=True)
def coordinator_trail(context):
    """Okruszki jako lista – ``web/coordinator/base.html`` chowa domyślny „eyebrow”, gdy okruszki są."""
    request = context.get("request")
    if request is None:
        return []
    return shell.navigation(request)["nav_trail"]
