"""``X-Djcms-Degraded: 1`` – znacznik odpowiedzi zbudowanej bez świeżych danych z API (§ 8.3).

Strona zdegradowana ma kod **200** (czytelnik dostaje ramę i treść redakcyjną, a zamiast danych
zawodów komunikat albo dane z kopii „stan na HH:MM”), więc po samym kodzie nie da się jej odróżnić
od zdrowej. Nagłówek jest dla monitoringu i testów akceptacyjnych (kryterium 7): mówi, że któraś
część tej odsłony nie dostała odpowiedzi „na teraz”. Ustawia go klient API
(``apps.live.client``, atrybut żądania), a tu tylko przepisujemy go na odpowiedź – po wyrenderowaniu
szablonu, bo dopiero szablon woła API.
"""

from __future__ import annotations

from .client import REQUEST_DEGRADED_ATTR

HEADER = "X-Djcms-Degraded"


class DegradedHeaderMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, REQUEST_DEGRADED_ATTR, False):
            response[HEADER] = "1"
        return response
