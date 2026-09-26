"""Tryb serwisu djcms: ``preview`` albo ``primary`` – z nagłówka Caddy'ego (DJ-02 D10, S7).

Który serwis obsługuje strony publiczne, rozstrzyga Caddy (``DJCMS_PRIMARY`` w ``.env`` +
``scripts/djcms_switch.sh``), a nie zmienna djcms: przełączenie ma działać bez restartu djcms.
Caddy dopisuje do każdego żądania przekazanego do djcms ``X-Djcms-Mode: preview|primary``
(``header_up`` – **nadpisuje** wartość od klienta), a djcms:

- ufa nagłówkowi **wyłącznie** z adresów ``TRUSTED_PROXY_IPS`` (``auth.is_trusted_proxy``) – ta sama
  reguła co ``X-Real-IP``. Podrobiony nagłówek od kogokolwiek innego jest ignorowany,
- w każdym innym przypadku (brak nagłówka, nieznana wartość, obcy adres) przyjmuje ``preview``.
  To jest bezpieczny kierunek błędu: ``preview`` = noindex, a pomyłka w drugą stronę wystawiłaby
  robotom wersję podglądową jako serwis główny.

Skutki trybu (tylko ``primary`` zdejmuje zakazy): ``X-Robots-Tag``, ``<meta name="robots">``,
``/robots.txt`` z ``Disallow: /``, brak ``/sitemap.xml`` i brak Google Analytics w ``preview``
(``apps.pages.middleware``, ``apps.pages.views``, ``templates/dj/base.html``).
"""

from __future__ import annotations

from .auth import is_trusted_proxy, trusted_proxy_networks

PREVIEW = "preview"
PRIMARY = "primary"
MODES = frozenset({PREVIEW, PRIMARY})

#: Nagłówek żądania (od Caddy'ego) i odpowiedzi (diagnostyka i testy E2E – ``web`` go nie ustawia).
HEADER = "X-Djcms-Mode"
META_KEY = "HTTP_X_DJCMS_MODE"

#: Atrybuty żądania ustawiane przez ``RequestModeMiddleware``. Szablony czytają
#: ``request.djcms_primary`` – brak atrybutu (żądanie, które warstwy nie przeszło) to fałsz,
#: czyli znowu noindex.
REQUEST_ATTR = "djcms_mode"
REQUEST_PRIMARY_ATTR = "djcms_primary"


def mode_from_request(request) -> str:
    """``primary`` wyłącznie z nagłówka zaufanego proxy; każda inna sytuacja to ``preview``."""
    value = (getattr(request, "META", {}).get(META_KEY) or "").strip().lower()
    if value == PRIMARY and is_trusted_proxy(request):
        return PRIMARY
    return PREVIEW


def request_mode(request) -> str:
    """Tryb ustalony przez warstwę (albo – gdy warstwa nie działała – policzony teraz)."""
    mode = getattr(request, REQUEST_ATTR, None)
    return mode if mode in MODES else mode_from_request(request)


def is_primary(request) -> bool:
    return request_mode(request) == PRIMARY


def proxy_request_meta(mode: str = PRIMARY) -> dict[str, str]:
    """Nagłówki żądania „jak od Caddy'ego” dla klienta testowego Django (``verify_cutover``, DJ-02e).

    ``{"HTTP_X_DJCMS_MODE": mode, "REMOTE_ADDR": <adres z pierwszej sieci TRUSTED_PROXY_IPS>}`` –
    wewnątrz procesu, bez sieci. Pusta lista zaufanych proxy = ``ImproperlyConfigured``: bez niej
    produkcyjny djcms też nie uwierzyłby Caddy'emu, więc kontrola nie może udawać, że tryb działa.
    """
    from django.core.exceptions import ImproperlyConfigured

    if mode not in MODES:
        raise ValueError(f"Nieznany tryb djcms: {mode!r}")
    networks = trusted_proxy_networks()
    if not networks:
        raise ImproperlyConfigured("TRUSTED_PROXY_IPS jest puste – djcms nie ufa żadnemu proxy (DJ-02 D10).")
    network = networks[0]
    address = network.network_address if network.num_addresses == 1 else next(network.hosts())
    return {META_KEY: mode, "REMOTE_ADDR": str(address)}
