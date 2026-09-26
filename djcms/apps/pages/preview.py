"""Ciasteczko podglądu ``djcms_view`` (DJ-02 D1, S8, S10).

Który serwis dostaje ścieżki publiczne danego hosta, rozstrzyga **Caddy** po tym ciasteczku:

- ``DJCMS_PRIMARY=0``: ``djcms_view=dj`` → djcms (``X-Djcms-Mode: preview``), inaczej ``web`` (Wagtail),
- ``DJCMS_PRIMARY=1``: ``djcms_view=wagtail`` → ``web`` (porównanie przed wycofaniem), inaczej djcms.

djcms tylko je **ustawia i kasuje** (``POST /djcms/preview/`` z CSRF) i czyta wyłącznie po to, żeby
na stronie podglądu pokazać bieżący wybór. Ciasteczko nie jest granicą bezpieczeństwa (treść jest
publiczna) i niczego nie odblokowuje w djcms ani w ``web``: nie zmienia uprawnień, trybu ani CSP.

Atrybuty: host-only (bez ``Domain``), ``Path=/`` (cały host – także konkursy pod prefiksem ścieżki
na hoście platformy), ``HttpOnly``, ``Secure`` (poza ``DEBUG`` na http), ``SameSite=Lax``, 8 h.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

COOKIE_NAME = "djcms_view"
COOKIE_MAX_AGE = 8 * 60 * 60
COOKIE_PATH = "/"

#: Wartości ciasteczka (dopasowuje je Caddy – ``scripts/render_caddyfile.sh``) i akcja kasowania.
VIEW_DJ = "dj"
VIEW_WAGTAIL = "wagtail"
VIEW_OFF = "off"
ACTIONS = frozenset({VIEW_DJ, VIEW_WAGTAIL, VIEW_OFF})


def current_view(request) -> str:
    """Bieżąca wartość ciasteczka (``dj``/``wagtail``) albo ``""`` – wyłącznie do opisu stanu."""
    value = request.COOKIES.get(COOKIE_NAME, "")
    return value if value in (VIEW_DJ, VIEW_WAGTAIL) else ""


def safe_next(value: str | None, default: str) -> str:
    """Adres powrotu: **wyłącznie** ścieżka na tym samym hoście, inaczej ``default`` (S8).

    Odrzuca adresy z hostem albo schematem (``https://evil``, ``//evil``, ``/\\evil``,
    ``javascript:…``) i znaki sterujące – przekierowanie po ``POST`` nie może wyprowadzić poza host.
    """
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return default
    if not url_has_allowed_host_and_scheme(value, allowed_hosts=None):
        return default
    return value


def apply(response, request, action: str) -> None:
    """Ustawia (``dj``/``wagtail``) albo kasuje (``off``) ciasteczko na odpowiedzi."""
    if action == VIEW_OFF:
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")
        return
    response.set_cookie(
        COOKIE_NAME,
        action,
        max_age=COOKIE_MAX_AGE,
        path=COOKIE_PATH,
        secure=request.is_secure() or not settings.DEBUG,
        httponly=True,
        samesite="Lax",
    )
