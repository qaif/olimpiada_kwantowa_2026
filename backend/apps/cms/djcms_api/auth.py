"""Bramki wewnętrznego API dla djcms.

**Bramki, w tej kolejności, każda porażka = pusta 404** (``docs/tasks/DJ-01.md`` § 3.1):

1. host żądania jest adresem wewnętrznym (``web``, ``localhost``, ``127.0.0.1``) – ta sama reguła,
   co ``/internal/tls-allowed`` (``apps.tenancy.internal_views.from_internal_host``). Z domeny
   publicznej adres wygląda więc tak, jakby go nie było – także z poprawnym tokenem, bo token mógł
   wyciec, a host publiczny oznacza, że żądanie przyszło z internetu, a nie z sieci compose'a,
2. token w ustawieniach ma co najmniej 32 znaki – krótszy albo pusty wyłącza API w całości
   (``cms.W010`` zgłasza krótki token w ``manage.py check``),
3. nagłówek ``X-Djcms-Token`` jest równy tokenowi. Porównanie w czasie stałym
   (``hmac.compare_digest``) – różnica czasu odpowiedzi nie może zdradzać, ile znaków się zgadza,
4. metoda to ``GET`` (zamiast ``require_GET``). Kończy się 404, a nie 405: ``require_GET`` stoi
   przed bramkami, więc jego 405 odpowiadałoby także z domeny publicznej i bez tokenu – czyli
   mówiłoby „tu coś jest” każdemu, kto wyśle ``POST``. API jest tylko do odczytu, a zaufany
   wołający innych metod nie używa, więc nie ma powodu, żeby ta porażka różniła się od reszty.

Pusta 404 jest odpowiedzią celowo nieodróżnialną od nieistniejącego adresu: brak treści, brak
szablonu z marką konkursu, ten sam kod dla każdego powodu.
"""

from __future__ import annotations

import hmac
from functools import wraps

from django.conf import settings
from django.http import HttpResponseNotFound

from apps.cms.checks import DJCMS_TOKEN_MIN_LENGTH
from apps.tenancy.internal_views import from_internal_host

#: Nagłówek z tokenem. W ``request.META`` Django trzyma go pod ``HTTP_X_DJCMS_TOKEN``.
TOKEN_HEADER = "X-Djcms-Token"


def api_enabled() -> bool:
    """Czy API w ogóle odpowiada – token ustawiony i nie krótszy niż ``DJCMS_TOKEN_MIN_LENGTH``."""
    return len(getattr(settings, "DJCMS_INTERNAL_TOKEN", "") or "") >= DJCMS_TOKEN_MIN_LENGTH


def token_matches(request) -> bool:
    """Czy nagłówek niesie token z ustawień. Porównanie w czasie stałym, na bajtach."""
    given = request.headers.get(TOKEN_HEADER, "") or ""
    expected = settings.DJCMS_INTERNAL_TOKEN or ""
    return hmac.compare_digest(given.encode(), expected.encode())


def request_allowed(request) -> bool:
    """Komplet bramek z docstringu modułu, od najtańszej. ``False`` = pusta 404."""
    return (
        from_internal_host(request) and api_enabled() and token_matches(request) and request.method == "GET"
    )


def internal_api(view):
    """Dekorator widoku API: bramki przed widokiem, pusta 404 przy każdej porażce."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request_allowed(request):
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)

    return wrapped
