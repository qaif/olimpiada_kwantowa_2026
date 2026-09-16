"""Publiczna strona statusu serwisu: ``/status/`` (HTML) i ``/status.json`` (monitoring).

Dwa adresy, jedno źródło danych (``apps.core.status.snapshot``) – rozjazd między tym, co widzi
człowiek, a tym, co odpytuje monitoring, znaczyłby, że jedno z dwojga kłamie.

Obie odpowiedzi są **buforowane na 30 sekund**. Nie dla wydajności strony, tylko dlatego, że jest
publiczna i nieuwierzytelniona: bez bufora byłaby jedynym adresem w serwisie, którym da się kazać
aplikacji odpytać bazę, cache, magazyn plików i Redisa – tyle razy na sekundę, ile wytrzyma sieć.
Trzydzieści sekund to zarazem rozdzielczość, przy której strona nadal odpowiada na pytanie
„czy to działa **teraz**”.

``/status.json`` jest osobnym adresem, a nie parametrem ``?format=json``: monitory zewnętrzne
konfiguruje się adresem, a część z nich rozpoznaje typ odpowiedzi po rozszerzeniu, zanim spojrzy
na nagłówek. Ten sam powód, co przy ``/me/calendar.ics``.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.generic import View

from apps.core.status import as_json, snapshot

TEMPLATE = "web/status.html"

#: Czas bufora obu wariantów. Patrz docstring modułu – to zabezpieczenie, nie optymalizacja.
CACHE_SECONDS = 30


@method_decorator(cache_page(CACHE_SECONDS), name="dispatch")
class StatusView(View):
    """``/status/`` – stan usług, czas serwera, stan zawodów i komunikaty organizatora."""

    def get(self, request):
        return TemplateResponse(request, TEMPLATE, snapshot())


@method_decorator(cache_page(CACHE_SECONDS), name="dispatch")
class StatusJsonView(View):
    """``/status.json`` – ten sam stan dla monitoringu zewnętrznego.

    Kod odpowiedzi jest **zawsze 200**, także przy awarii podsystemu, a werdykt niesie pole
    ``status``. Inaczej niż ``/healthz/``, które oddaje 503 dla orkiestratora: tam kod HTTP jest
    poleceniem („nie kieruj tu ruchu”), tutaj byłby nieporozumieniem – monitor, który dostaje 503,
    zwykle uznaje, że niedostępna jest sama strona statusu, i przestaje czytać jej treść.
    """

    def get(self, request):
        return JsonResponse(as_json(snapshot()))
