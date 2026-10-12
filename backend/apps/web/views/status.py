"""Publiczna strona statusu serwisu: ``/status/`` (HTML) i ``/status.json`` (monitoring).

Dwa adresy, jedno źródło danych (``apps.core.status.snapshot``) – rozjazd między tym, co widzi
człowiek, a tym, co odpytuje monitoring, znaczyłby, że jedno z dwojga kłamie.

Oba warianty mają **bufor 30 sekund**. Nie dla wydajności strony, tylko dlatego, że jest
publiczna i nieuwierzytelniona: bez bufora byłaby jedynym adresem w serwisie, którym da się kazać
aplikacji odpytać bazę, cache, magazyn plików i Redisa – tyle razy na sekundę, ile wytrzyma sieć.
Trzydzieści sekund to zarazem rozdzielczość, przy której strona nadal odpowiada na pytanie
„czy to działa **teraz**”.

**Wariant HTML buforuje dane, a nie odpowiedź** (audyt 10.10.2026, W1). Strona dziedziczy po
``base.html``, który wypisuje e-mail zalogowanego, jego role, komunikaty flash i token CSRF.
``cache_page`` na widoku liczy klucz z jeszcze nierenderowanej odpowiedzi – zanim warstwy sesji
i CSRF dołożą ``Vary: Cookie`` – więc klucz zależał tylko od adresu i języka, a anonim dostawał
stronę wyrenderowaną dla ostatniego zalogowanego. Do tego ``Cache-Control: max-age=30`` bez
``private`` pozwalał zapisać ją pośredniczącemu proxy. Teraz w cache'u leży wyłącznie wynik
``snapshot()`` (nic w nim nie zależy od użytkownika), a strona renderuje się na żywo przy każdym
żądaniu.

``/status.json`` zostaje przy ``cache_page``: ciało buduje ``as_json`` z samego snapshotu i stanu
kopii, bez szablonu bazowego i bez czegokolwiek z ``request.user`` – odpowiedź jest identyczna dla
każdego, więc jej współdzielenie jest bezpieczne.

``/status.json`` jest osobnym adresem, a nie parametrem ``?format=json``: monitory zewnętrzne
konfiguruje się adresem, a część z nich rozpoznaje typ odpowiedzi po rozszerzeniu, zanim spojrzy
na nagłówek. Ten sam powód, co przy ``/me/calendar.ics``.
"""

from __future__ import annotations

from django.core.cache import cache
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.decorators import method_decorator
from django.utils.translation import get_language
from django.views.decorators.cache import cache_page
from django.views.generic import View

from apps.core.status import as_json, snapshot

TEMPLATE = "web/status.html"

#: Czas bufora obu wariantów. Patrz docstring modułu – to zabezpieczenie, nie optymalizacja.
CACHE_SECONDS = 30


def snapshot_cache_key() -> str:
    """Klucz bufora danych strony: konkurs z kontekstu żądania i język.

    Konkurs, bo strona statusu jest per host (``competition_state``). Język, bo w snapshocie są
    już przetłumaczone zdania (szczegół kolejki, komunikat rejestracji) – bez niego wersja
    angielska dostałaby polskie zdania z bufora, albo odwrotnie.
    """
    from apps.tenancy.context import current_competition

    competition = current_competition()
    competition_id = competition.pk if competition is not None else "none"
    return f"status:snapshot:{competition_id}:{get_language() or ''}"


def cached_snapshot() -> dict:
    """``snapshot()`` z buforem 30 s. W buforze nie ma niczego, co zależy od użytkownika."""
    return cache.get_or_set(snapshot_cache_key(), snapshot, CACHE_SECONDS)


class StatusView(View):
    """``/status/`` – stan usług, czas serwera, stan zawodów i komunikaty organizatora."""

    def get(self, request):
        # Czas serwera bierzemy na żywo, nie z bufora: to on rozstrzyga o przyjęciu pracy i nie
        # ma powodu, żeby strona pokazywała go z opóźnieniem do 30 s. Reszta danych (werdykty
        # usług, stan zawodów) może mieć te 30 s – patrz docstring modułu.
        context = {**cached_snapshot(), "now": timezone.now()}
        response = TemplateResponse(request, TEMPLATE, context)
        # Strona jest personalizowana (nagłówek z e-mailem, flash, CSRF) – żadna pamięć po drodze
        # (przeglądarka współdzielona, proxy) nie może jej zachować ani oddać komuś innemu.
        add_never_cache_headers(response)
        return response


@method_decorator(cache_page(CACHE_SECONDS), name="dispatch")
class StatusJsonView(View):
    """``/status.json`` – ten sam stan dla monitoringu zewnętrznego.

    Kod odpowiedzi jest **zawsze 200**, także przy awarii podsystemu, a werdykt niesie pole
    ``status``. Inaczej niż ``/healthz/``, które oddaje 503 dla orkiestratora: tam kod HTTP jest
    poleceniem („nie kieruj tu ruchu”), tutaj byłby nieporozumieniem – monitor, który dostaje 503,
    zwykle uznaje, że niedostępna jest sama strona statusu, i przestaje czytać jej treść.

    ``cache_page`` jest tu bezpieczny: ciało nie zależy od użytkownika ani od sesji (patrz
    docstring modułu), więc współdzielony wpis jest dla każdego taki sam.
    """

    def get(self, request):
        return JsonResponse(as_json(snapshot()))
