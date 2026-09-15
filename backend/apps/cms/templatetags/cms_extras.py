"""Znaczniki szablonów części informacyjnej. Dziś jeden: pasek linii czasu w nagłówku.

Znacznik, a nie procesor kontekstu, i to jest decyzja, nie przypadek. Procesor kontekstu odpala
się przy **każdym** renderowaniu szablonu – także fragmentu HTMX i strony błędu – niezależnie od
tego, czy cokolwiek go użyje; pasek liczyłby się wtedy również tam, gdzie nagłówka nie ma.
Znacznik liczy się dokładnie raz, w miejscu, w którym stoi w ``templates/base.html``, i znika
razem z blokiem, który szablon potomny może wyłączyć.

Błąd bazy nie może wywrócić szablonu bazowego – tak samo, jak w ``apps.web.context_processors``.
Pasek jest ozdobą nagłówka: brak bieżącej edycji albo baza bez migracji oznacza stronę bez paska,
a nie stronę z pięćsetką.
"""

from __future__ import annotations

import logging

from django import template
from django.db import DatabaseError

from ..timeline import timeline_strip

logger = logging.getLogger(__name__)

register = template.Library()


@register.inclusion_tag("cms/_timeline_strip.html", takes_context=True)
def timeline_strip_block(context) -> dict:
    """Dane paska dla ``templates/cms/_timeline_strip.html``.

    ``takes_context``, bo szablon paska potrzebuje ``request`` – po to, żeby odnośnik prowadzący
    do strony, na której czytelnik już stoi, dostał ``aria-current``. Reszta danych pochodzi
    wyłącznie z bazy i z zegara.
    """
    try:
        strip = timeline_strip()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
        logger.warning("Nie udało się policzyć linii czasu dla nagłówka.")
        strip = None
    return {"strip": strip, "request": context.get("request")}
