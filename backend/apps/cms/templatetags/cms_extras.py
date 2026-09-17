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

from ..tenancy import competition_for_request
from ..timeline import timeline_strip

logger = logging.getLogger(__name__)

register = template.Library()


@register.inclusion_tag("cms/_timeline_strip.html", takes_context=True)
def timeline_strip_block(context) -> dict:
    """Dane paska dla ``templates/cms/_timeline_strip.html``.

    ``takes_context``, bo szablon paska potrzebuje ``request`` – po to, żeby odnośnik prowadzący
    do strony, na której czytelnik już stoi, dostał ``aria-current``. Od zakresowania części
    informacyjnej żądanie odpowiada tu na drugie pytanie: **czyj** to harmonogram. Pasek wisi
    w nagłówku każdej strony serwisu, więc wzięcie edycji „pierwszej z brzegu” znaczyłoby
    ogłaszanie terminów jednej olimpiady pod domeną drugiej.

    Konkurs bierzemy z ``request.competition`` (ustawia je ``CompetitionMiddleware``), czyli bez
    zapytania do bazy. Szablon renderowany bez żądania – strona błędu, podgląd, test jednostkowy –
    schodzi na konkurs z kontekstu, a gdy i tego nie ma, pasek po prostu nie powstaje.
    """
    request = context.get("request")
    try:
        strip = timeline_strip(competition=competition_for_request(request) if request else None)
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
        logger.warning("Nie udało się policzyć linii czasu dla nagłówka.")
        strip = None
    return {"strip": strip, "request": request}
