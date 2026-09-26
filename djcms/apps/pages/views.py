"""Widoki aplikacyjne djcms spoza drzewa stron: healthcheck i ``robots.txt``."""

from __future__ import annotations

import logging

from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def _ping_database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


@never_cache
@require_GET
def healthz(request):
    """Healthcheck compose'a: ``SELECT 1`` na bazie djcms – i **nic więcej**.

    Świadomie **nie** pyta API aplikacji głównej: niedostępny ``web`` to stan, w którym djcms ma
    działać dalej i degradować sekcje żywe (§ 8.3), a nie kontener „chory” do restartu. Gdyby
    healthcheck zależał od API, awaria głównego serwisu restartowałaby w pętli także ``dj.``.
    """
    try:
        db_ok = _ping_database()
    except Exception:  # noqa: BLE001 - każdy błąd bazy = „niezdrowy”, szczegóły tylko w logu
        logger.warning("healthz: baza djcms niedostępna", exc_info=True)
        db_ok = False
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=200 if db_ok else 503)


@require_GET
def robots_txt(request):
    """``Disallow: /`` dla wszystkich – trzecia droga noindex obok nagłówka i meta (reguła 10)."""
    return HttpResponse(ROBOTS_TXT, content_type="text/plain; charset=utf-8")
