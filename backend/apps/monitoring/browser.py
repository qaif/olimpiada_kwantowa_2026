"""Błędy JavaScriptu w GlitchTipie (OPS-02 § 5) – konfiguracja loadera i źródło dla CSP.

Funkcja jest **osobno** włączana (``SENTRY_BROWSER=1``) i ma osobny DSN (``SENTRY_BROWSER_DSN``),
który domyślnie jest tym samym co ``SENTRY_DSN``. Klucz w DSN jest publiczny z założenia protokołu
(pozwala wyłącznie wysłać zdarzenie), ale trafia do HTML-a każdej strony – dlatego wolno go podać
osobno (np. drugi projekt w GlitchTipie z własnym limitem), a backend może wtedy używać adresu
wewnętrznego (``http://<klucz>@glitchtip:8000/<projekt>``).

Bez włączonej funkcji obie funkcje oddają „nic”: znacznik ``<script>`` jest pustym napisem,
a ``connect-src`` nie dostaje żadnego originu – nagłówek CSP jest co do bajtu dawny.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings


@dataclass(frozen=True)
class BrowserConfig:
    origin: str
    endpoint: str


def parse_dsn(dsn: str) -> BrowserConfig | None:
    """``https://<klucz>@errors.example.org/<projekt>`` → origin i adres koperty Sentry.

    Zły DSN (bez klucza, bez numeru projektu, nie-``https``, host bez kropki) = ``None``, czyli
    funkcja wyłączona – a nie znacznik z adresem, który przeglądarka i tak odrzuci.
    """
    try:
        parts = urlsplit((dsn or "").strip())
        port = parts.port
    except ValueError:
        return None
    # Wyłącznie ``https`` i nazwa z kropką: przeglądarka uczestnika nie dosięgnie ``glitchtip:8000``
    # (DSN wewnętrzny serwera), a ``http`` z naszej strony HTTPS to mieszana treść i klucz jawnym tekstem.
    if parts.scheme != "https" or not parts.username or not parts.hostname or "." not in parts.hostname:
        return None
    prefix, _, project = parts.path.rstrip("/").rpartition("/")
    if not project.isdigit():
        return None
    host = parts.hostname + (f":{port}" if port else "")
    origin = f"{parts.scheme}://{host}"
    endpoint = f"{origin}{prefix}/api/{project}/envelope/?sentry_key={parts.username}&sentry_version=7"
    return BrowserConfig(origin=origin, endpoint=endpoint)


def browser_config() -> BrowserConfig | None:
    if not getattr(settings, "SENTRY_BROWSER", False):
        return None
    dsn = getattr(settings, "SENTRY_BROWSER_DSN", "") or getattr(settings, "SENTRY_DSN", "")
    return parse_dsn(dsn)


def connect_sources() -> tuple[str, ...]:
    """Origin GlitchTipa do ``connect-src`` – wyłącznie przy włączonej funkcji."""
    config = browser_config()
    return (config.origin,) if config else ()
