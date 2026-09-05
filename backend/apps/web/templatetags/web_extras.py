"""Drobne filtry szablonów interfejsu WWW.

Świadomie ubogie: żaden z tych filtrów nie generuje HTML-a i żaden nie oznacza wyniku jako
bezpiecznego (``mark_safe``). Wszystko, co pochodzi od użytkownika, wychodzi z szablonu przez
domyślne autoescapowanie.
"""

from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """Odczyt ``mapping[key]`` dla klucza wyliczonego w pętli (Django nie ma tego wbudowanego).

    Klucze punktów w snapshotcie wyników są tekstowe (``"1"``, ``"2"``), a numery zadań bywają
    liczbami – stąd druga próba po konwersji na tekst.
    """
    if not hasattr(mapping, "get"):
        return None
    value = mapping.get(key)
    if value is None:
        value = mapping.get(str(key))
    return value
