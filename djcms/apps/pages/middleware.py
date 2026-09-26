"""Nagłówki djcms: tryb (``X-Djcms-Mode``), ``Content-Security-Policy``, ``X-Robots-Tag``, ``Cache-Control``.

**CSP** (reguła 6 z § 7 i § 8.4 docs/tasks/DJ-01.md). Dwie polityki, jak w aplikacji głównej
(``backend/apps/web/middleware.py`` – tam pełne uzasadnienie dyrektyw):

- **publiczna** (anonim i każdy, kto nie jest personelem): nonce + ``'strict-dynamic'``, bez
  ``'unsafe-inline'``/``'unsafe-eval'`` w ``script-src``. Co najmniej tak szczelna jak strona
  główna – djcms odpowiada pod tymi samymi hostami co aplikacja (DJ-02 D1), więc nie może być
  słabszym wejściem do tej samej domeny. Publiczne szablony ładują wyłącznie własne skrypty
  z ``static/js`` z atrybutem ``nonce`` (bez HTMX i Alpine'a), więc lista CDN-ów z backendu tu
  nie występuje. Jedyny wyjątek to Google Analytics 4 (niżej),
- **redaktora** (ścieżka panelu albo zalogowany personel): ``'unsafe-inline'``/``'unsafe-eval'``,
  bo pasek narzędzi django CMS, edytor djangocms-text i panel filera wstrzykują skrypty inline.
  Personel dostaje ją także na stronach publicznych, bo tam właśnie działa pasek narzędzi
  i tryb edycji. Ryzyko jest ograniczone zakresem jak w backendzie: wymaga zalogowania
  z ``is_staff``, a treść od anonimów w djcms nie istnieje. W tej polityce **nie ma** nonce'a:
  przeglądarka, widząc nonce, ignoruje ``'unsafe-inline'`` – panel byłby martwy.

Decyzja zapada **po** odpowiedzi: dopiero wtedy wiadomo, kim jest użytkownik
(``AuthenticationMiddleware`` stoi wewnątrz tego middleware). Nonce powstaje **przed** widokiem,
bo szablon musi go mieć w chwili renderowania.

Prefiks panelu nie jest literałem – pochodzi z ``reverse("admin:index")``, więc przeniesienie
panelu w ``config/urls.py`` nie zostawia luźnej polityki pod starym adresem.

**Google Analytics 4** (DJ-02 § 8): hosty GA dochodzą do polityki publicznej **wyłącznie** dla
odpowiedzi, której szablon naprawdę wstawił tag (``request.dj_analytics`` – ustawia go
``apps.pages.seo.analytics_id``: tryb ``primary``, identyfikator w ``chrome``, nie personel). Te
same trzy listy co ``backend/apps/web/middleware.py`` (tam uzasadnienie każdej); bez GA nagłówek
jest co do bajtu taki jak przed jego dodaniem.

**Tryb** (DJ-02 D10, S7 – ``apps.pages.mode``): ``RequestModeMiddleware`` ustala ``preview``/``primary``
z nagłówka Caddy'ego (wyłącznie od ``TRUSTED_PROXY_IPS``) i odsyła go w odpowiedzi.

**noindex** (reguła 10 DJ-01, zmieniona przez S7 DJ-02): ``X-Robots-Tag: noindex, nofollow, noarchive``
na **każdej** odpowiedzi w trybie ``preview`` (także 404, 500, statykach i panelu), a w ``primary``
wyłącznie pod ``/djcms/`` (panel, podgląd, SSO, healthcheck – adresy aplikacyjne, nie treść). To
jedna z trzech dróg (obok ``<meta name="robots">`` i ``/robots.txt``) i jedyna, która działa dla
odpowiedzi innych niż HTML.

**Bufor** (DJ-02 § 8): publiczny HTML dostaje ``Cache-Control: private, no-store`` – niesie
jednorazowy nonce CSP i dane żywe z API (ten sam wybór co ``apps/web/page_cache.py``). Odpowiedzi
nie-HTML (``sitemap.xml``, ``robots.txt``, statyki WhiteNoise) ustawiają bufor same.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.cache import patch_cache_control

from . import mode
from .seo import ANALYTICS_REQUEST_ATTR

NONCE_BYTES = 16

#: Hosty ramek osadzeń – te same trzy co ``EMBED_FRAME_SOURCES`` backendu. Wtyczka ``Embed``
#: (DJ-01e) buduje ``<iframe>`` z wzorca wyłącznie dla YouTube'a (``youtube-nocookie``) i Vimeo.
EMBED_FRAME_SOURCES = (
    "https://www.youtube.com",
    "https://www.youtube-nocookie.com",
    "https://player.vimeo.com",
)

ROBOTS_HEADER = "X-Robots-Tag"
ROBOTS_VALUE = "noindex, nofollow, noarchive"

FALLBACK_ADMIN_PREFIX = "/djcms/admin/"

#: Adresy aplikacyjne djcms (DJ-02 D2) – noindex także w trybie ``primary``.
APP_PATH_PREFIX = "/djcms/"

#: Hosty Google Analytics 4 – kopia ``ANALYTICS_*`` z ``backend/apps/web/middleware.py``.
ANALYTICS_SCRIPT_SOURCES = ("https://www.googletagmanager.com",)
ANALYTICS_CONNECT_SOURCES = (
    "https://*.google-analytics.com",
    "https://*.analytics.google.com",
    "https://www.googletagmanager.com",
)
ANALYTICS_IMG_SOURCES = ("https://*.google-analytics.com", "https://www.googletagmanager.com")


def media_origin() -> str:
    """Origin publicznego kubełka głównego serwisu (logo organizatora, slider). Pusty = brak."""
    parts = urlsplit((getattr(settings, "DJCMS_MAIN_MEDIA_ORIGIN", "") or "").strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def _sources(*items: str) -> str:
    return " ".join(item for item in items if item)


def build_public_policy(nonce: str, *, analytics: bool = False) -> str:
    """Polityka stron publicznych – ścisła, z jednorazowym nonce'em (§ 8.4); ``analytics`` = hosty GA4."""
    media = media_origin()
    ga_scripts = ANALYTICS_SCRIPT_SOURCES if analytics else ()
    ga_images = ANALYTICS_IMG_SOURCES if analytics else ()
    ga_connect = ANALYTICS_CONNECT_SOURCES if analytics else ()
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            f"img-src {_sources("'self'", 'data:', media, *ga_images)}",
            f"media-src {_sources("'self'", media)}",
            "font-src 'self' data:",
            # Wyjątek wyłącznie dla stylów – jak w backendzie; styl inline nie wykonuje kodu.
            "style-src 'self' 'unsafe-inline'",
            # Nonce (i host GA – fallback CSP2, jak w backendzie) przed 'strict-dynamic': przeglądarka
            # CSP2 pominie nieznane słowo kluczowe i zostanie przy 'self' + nonce, CSP3 zaufa
            # wyłącznie nonce'owi.
            f"script-src {_sources("'self'", f"'nonce-{nonce}'", *ga_scripts, "'strict-dynamic'")}",
            f"connect-src {_sources("'self'", *ga_connect)}",
            f"frame-src {' '.join(EMBED_FRAME_SOURCES)}",
            "worker-src 'self'",
        ]
    )


def build_editor_policy() -> str:
    """Polityka panelu i zalogowanego personelu – świadomie luźna dla skryptów (patrz docstring)."""
    media = media_origin()
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            # Tryb struktury i okna modalne django CMS osadzają własne adresy w ramce tej domeny.
            "frame-ancestors 'self'",
            "form-action 'self'",
            f"img-src {_sources("'self'", 'data:', 'blob:', media)}",
            "media-src 'self' data: blob:",
            "font-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            f"frame-src 'self' {' '.join(EMBED_FRAME_SOURCES)}",
        ]
    )


def admin_prefix() -> str:
    try:
        return reverse("admin:index")
    except NoReverseMatch:  # pragma: no cover - panel jest zawsze w config/urls.py
        return FALLBACK_ADMIN_PREFIX


def is_editor_request(request) -> bool:
    """Ścieżka panelu albo zalogowany personel (§ 8.4).

    ``request.user`` może nie istnieć: odpowiedź WhiteNoise (statyki) albo odmowa zanim
    ``AuthenticationMiddleware`` zdążył zadziałać. Brak użytkownika = polityka publiczna,
    czyli ta ściślejsza – błąd w tę stronę niczego nie otwiera.
    """
    if request.path.startswith(admin_prefix()):
        return True
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and user.is_staff)


def _is_html(response) -> bool:
    return (response.get("Content-Type") or "").lower().startswith("text/html")


class ContentSecurityPolicyMiddleware:
    """Nadaje żądaniu ``csp_nonce``, dokleja nagłówek CSP i ``Cache-Control`` HTML-a."""

    header = "Content-Security-Policy"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nonce = secrets.token_urlsafe(NONCE_BYTES)
        request.csp_nonce = nonce
        response = self.get_response(request)
        if is_editor_request(request):
            response[self.header] = build_editor_policy()
            # Strona oglądana przez redaktora niesie pasek narzędzi, wersje robocze i tokeny
            # formularzy – nie może trafić do żadnego bufora pośredniego ani do bufora przeglądarki.
            patch_cache_control(response, private=True, no_store=True)
        else:
            analytics = bool(getattr(request, ANALYTICS_REQUEST_ATTR, False))
            response[self.header] = build_public_policy(nonce, analytics=analytics)
            if _is_html(response):
                # Nonce i dane żywe – HTML nie może trafić do żadnego bufora (DJ-02 § 8).
                patch_cache_control(response, private=True, no_store=True)
        return response


class RequestModeMiddleware:
    """``request.djcms_mode``/``request.djcms_primary`` z nagłówka Caddy'ego + ``X-Djcms-Mode`` w odpowiedzi.

    Stoi zaraz za ``SecurityMiddleware``, przed rozstrzyganiem witryny: tryb jest cechą przeskoku
    przez proxy, a nie konkursu, więc ma go także pusta 404 nieznanego hosta (razem z noindex).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        current = mode.mode_from_request(request)
        setattr(request, mode.REQUEST_ATTR, current)
        setattr(request, mode.REQUEST_PRIMARY_ATTR, current == mode.PRIMARY)
        response = self.get_response(request)
        response[mode.HEADER] = current
        return response


class NoIndexMiddleware:
    """``X-Robots-Tag: noindex…`` – w ``preview`` zawsze, w ``primary`` pod ``/djcms/``."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not mode.is_primary(request) or request.path_info.startswith(APP_PATH_PREFIX):
            response[ROBOTS_HEADER] = ROBOTS_VALUE
        return response
