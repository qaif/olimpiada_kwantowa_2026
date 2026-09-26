"""Procesor kontekstu ramy: ``dj_chrome`` dla ``dj/base.html``.

Obiekt jest leniwy (``apps.live.chrome.LiveChrome``) – samo dołożenie procesora nie wysyła żadnego
żądania do API; robi to dopiero szablon, który sięga po dane ramy.
"""

from __future__ import annotations

from .chrome import LiveChrome


def chrome(request) -> dict:
    return {"dj_chrome": LiveChrome(request)}
