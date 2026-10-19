"""Sprawdzenie systemowe ``webtex.W001``: klucz integracji za krótki albo równy innemu sekretowi.

Ostrzeżenie, a nie błąd – wzór ``cms.W013``: za krótki klucz działa jak pusty (edytor jest wtedy
wyłączony), więc serwis startuje, a operator widzi powód w ``manage.py check``.
"""

from __future__ import annotations

from django.core.checks import Warning as CheckWarning
from django.core.checks import register

from .keys import key_problems


@register()
def webtex_shared_key(app_configs, **kwargs):
    return [CheckWarning(problem, id="webtex.W001") for problem in key_problems()]
