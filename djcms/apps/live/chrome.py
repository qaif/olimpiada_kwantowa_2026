"""Rama serwisu (nagłówek, pasek konta, menu, stopka) z endpointu ``chrome`` – § 8.5 docs/tasks/DJ-01.md.

``LiveChrome`` to leniwe opakowanie odpowiedzi ``chrome`` dla szablonu ``dj/base.html``. Leniwe,
bo procesor kontekstu działa przy **każdym** renderowaniu z ``RequestContext`` – także w panelu
admina i przy stronach błędów, które ramy nie rysują. API pytamy dopiero wtedy, gdy szablon
sięgnie po pierwsze pole.

Wszystko, czego rama potrzebuje „na pewno” (nazwa serwisu, odnośnik logowania), ma tu wartość
zapasową, więc przy martwym API rama wygląda jak zwykle, tylko bez komunikatów, slidera, paska
osi czasu, przycisku rejestracji i danych organizatora (§ 8.3 „Degradacja”). Odnośniki zapasowe
prowadzą pod adres publiczny **konkursu żądania** (``CompetitionSite.public_base`` z rejestru –
ten sam host i prefiks, na którym działa aplikacja główna konkursu; DJ-02 § 8 „Odnośniki
aplikacji”), a gdy konkurs go nie ma – na ``DJCMS_MAIN_PUBLIC_URL``.
"""

from __future__ import annotations

from functools import cached_property
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone
from django.utils.encoding import escape_uri_path

from . import client

#: Ścieżka logowania aplikacji głównej – wyłącznie na wypadek braku odpowiedzi API (normalnie
#: adres przychodzi z ``chrome.links.login``, policzony ``reverse()`` po stronie aplikacji).
FALLBACK_LOGIN_PATH = "/login/"


def public_base(request=None) -> str:
    """Baza adresów aplikacji głównej dla żądania: ``public_base`` konkursu albo ``DJCMS_MAIN_PUBLIC_URL``."""
    site = getattr(request, "competition_site", None) if request is not None else None
    base = getattr(site, "public_base", "") or settings.DJCMS_MAIN_PUBLIC_URL or ""
    return base.rstrip("/")


def main_url(path: str = "/", request=None) -> str:
    """Adres w aplikacji głównej konkursu: baza (``public_base``) + ścieżka od korzenia konkursu."""
    return f"{public_base(request)}{path if path.startswith('/') else '/' + path}"


def request_page_path(request) -> str:
    """Ścieżka bieżącej strony liczona od korzenia **konkursu** (bez prefiksu), bezpieczna w adresie."""
    return escape_uri_path(request.path_info) if request is not None else "/"


#: Klucze odpowiedzi ``chrome``, których wartości trafiają do ``href``/``src``.
URL_KEYS = frozenset({"url", "src", "link_url", "contact_url", "og_image"})


def safe_href(value) -> str:
    """Adres, który wolno wstawić do ``href``/``src``: ścieżka ``/…`` albo ``http(s)://host…``.

    API już filtruje adresy (``api_href``/``safe_http_url`` po stronie aplikacji głównej); to jest
    druga warstwa po stronie ``dj.``: autoescape chroni przed wyjściem z atrybutu, ale nie przed
    ``javascript:`` w ``href``. Odpowiedź API nie jest źródłem, któremu szablon ma ufać ślepo.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    # Białe znaki i znaki sterujące w środku adresu – odmowa: przeglądarka wycina tabulator i nowe
    # wiersze z adresu (WHATWG URL), więc ``/\t/evil.example`` staje się ``//evil.example`` – adresem
    # bez schematu, czyli innym hostem. Poprawny adres ma je zakodowane (``%20``).
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return ""
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    parts = urlsplit(value)
    if parts.scheme.lower() in ("http", "https") and parts.netloc:
        return value
    return ""


def _clean(value, key: str | None = None):
    """Kopia struktury z każdym adresem przepuszczonym przez ``safe_href``."""
    if isinstance(value, dict):
        in_links = key == "links"
        return {
            k: (safe_href(v) if (k in URL_KEYS or in_links) and isinstance(v, str) else _clean(v, k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _clean_announcements(items) -> list[dict]:
    """Komunikat z adresem odrzuconym przez ``safe_href`` pokazuje się bez odnośnika (jak w API)."""
    cleaned = []
    for item in items or []:
        if isinstance(item, dict):
            cleaned.append({**item, "has_link": bool(item.get("has_link") and item.get("link_url"))})
    return cleaned


def stale_label(result: client.ApiResult | None) -> str:
    """„stan na HH:MM” dla danych z kopii zapasowej; pusty napis dla danych świeżych."""
    if result is None or not result.stale or result.fetched_at is None:
        return ""
    return f"stan na {timezone.localtime(result.fetched_at):%H:%M}"


class LiveChrome:
    """Dane ramy dla szablonu. Pola odpowiadają kluczom ``chrome`` z § 3.3 (kształt z implementacji
    ``backend/apps/cms/djcms_api/views.py::chrome``)."""

    def __init__(self, request):
        self.request = request

    @cached_property
    def result(self) -> client.ApiResult:
        return client.get("chrome", request=self.request)

    @property
    def available(self) -> bool:
        return self.result.ok

    @property
    def stale(self) -> bool:
        return self.result.stale

    @property
    def stale_label(self) -> str:
        return stale_label(self.result)

    @cached_property
    def data(self) -> dict:
        return _clean(self.result.data or {})

    # --- pola używane przez ramę -------------------------------------------------------------------

    @property
    def site(self) -> dict:
        return self.data.get("site") or {}

    @property
    def site_name(self) -> str:
        return self.site.get("site_name") or settings.DJCMS_FALLBACK_SITE_NAME

    @property
    def edition(self) -> dict | None:
        return self.data.get("edition")

    @property
    def registration(self) -> dict:
        return self.data.get("registration") or {}

    @property
    def links(self) -> dict:
        return self.data.get("links") or {}

    @property
    def login_url(self) -> str:
        return self.links.get("login") or main_url(FALLBACK_LOGIN_PATH, self.request)

    @property
    def announcements(self) -> list:
        return _clean_announcements(self.data.get("announcements"))

    @property
    def sponsor_slider(self) -> dict:
        return self.data.get("sponsor_slider") or {}

    @property
    def timeline_strip(self) -> dict | None:
        return self.data.get("timeline_strip")

    @property
    def supervisor_menu(self) -> list[dict]:
        """Pozycje listy „Dla szkół/nauczycieli” – port ``_supervisor_menu_item`` z backendu.

        Na ``dj.`` nikt nie jest zalogowany do aplikacji głównej (osobne konta), więc warunek
        „tylko dla niezalogowanych” jest spełniony zawsze; zostaje przełącznik witryny
        (``supervisor_registration.enabled``) i dostępność plakatów (``links.posters``).
        """
        items = []
        supervisor = self.data.get("supervisor_registration") or {}
        if supervisor.get("enabled") and supervisor.get("url"):
            items.append({"title": "Rejestracja nauczyciela", "url": supervisor["url"]})
        if self.links.get("posters"):
            items.append({"title": "Plakaty do pobrania", "url": self.links["posters"]})
        return items

    @property
    def main_home_url(self) -> str:
        return self.links.get("main_home") or main_url("/", self.request)

    @property
    def main_page_url(self) -> str:
        """Ta sama ścieżka na domenie głównej – „Ta strona w wersji Wagtail” w stopce."""
        return main_url(request_page_path(self.request), self.request)
