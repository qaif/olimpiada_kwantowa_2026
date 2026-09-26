"""Znaczniki szablonów dla danych na żywo (``{% load dj_live %}``).

- ``{% dj_unavailable %}`` – komunikat „dane chwilowo niedostępne” z odnośnikiem do tej samej
  ścieżki na domenie głównej (§ 8.3 „Degradacja”). Używają go sekcje żywe (DJ-01f), gdy API nie
  oddało danych ani z bufora, ani z kopii,
- ``result|dj_stale_label`` – dopisek „stan na HH:MM” przy danych z kopii zapasowej,
- ``"/sciezka/"|dj_main_url`` – adres na domenie głównej.
"""

from __future__ import annotations

from django import template
from django.utils.encoding import escape_uri_path

from ..chrome import main_url, stale_label

register = template.Library()


@register.inclusion_tag("dj/partials/_unavailable.html", takes_context=True)
def dj_unavailable(context, path: str | None = None) -> dict:
    request = context.get("request")
    if path is None:
        path = escape_uri_path(request.path) if request is not None else "/"
    return {"main_url": main_url(path)}


@register.filter
def dj_stale_label(result) -> str:
    return stale_label(result)


@register.filter
def dj_main_url(path: str) -> str:
    return main_url(str(path or "/"))
