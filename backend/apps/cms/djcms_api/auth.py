"""Bramki wewnętrznego API i wybór konkursu, którego dane API oddaje.

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
from typing import TYPE_CHECKING

from django.conf import settings
from django.http import HttpResponseNotFound

from apps.cms.checks import DJCMS_TOKEN_MIN_LENGTH
from apps.cms.tenancy import competition_for_site
from apps.tenancy.internal_views import from_internal_host

if TYPE_CHECKING:  # pragma: no cover - wyłącznie dla podpowiedzi typów
    from apps.tenancy.models import Competition

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


def djcms_competition() -> Competition | None:
    """Konkurs, którego dane oddaje API – albo ``None`` (API odpowiada wtedy 503).

    ``CompetitionMiddleware`` celowo **nie** rozstrzyga konkursu dla ``/internal/*``: wołający puka
    z nagłówkiem ``Host: web:8000``, który z żadnym konkursem nie ma nic wspólnego. Konkurs wybiera
    więc konfiguracja, a nie żądanie:

    - ``DJCMS_COMPETITION_SLUG`` ustawiony – ten konkurs, o ile jest aktywny. Nieaktywny albo
      nieistniejący daje ``None``, a nie „pierwszy z brzegu”: wersja ``dj.`` ma pokazać komunikat
      o niedostępności, a nie cudze terminy,
    - pusty – konkurs witryny **domyślnej**, czyli ten, który stoi pod ``SITE_DOMAIN`` (Konkurs #1).
      To ta sama witryna, którą Wagtail wybiera dla hosta, którego nie zna.
    """
    from wagtail.models import Site

    from apps.tenancy.models import Competition

    slug = (getattr(settings, "DJCMS_COMPETITION_SLUG", "") or "").strip()
    if slug:
        return Competition.objects.filter(slug=slug, is_active=True).select_related("site").first()
    site = Site.objects.filter(is_default_site=True).first()
    competition = competition_for_site(site)
    return competition if competition is not None and competition.is_active else None
