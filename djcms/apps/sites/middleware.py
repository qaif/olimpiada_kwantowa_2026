"""Warstwa witryny konkursu: host + prefiks → ``request.site`` / ``request.competition_site`` (DJ-02 § 5.3).

Stoi zaraz za ``SecurityMiddleware``, **przed** wszystkim, co czyta witrynę (CSP i szablony przez
procesor kontekstu, django CMS, menu, bufor placeholderów). Robi cztery rzeczy:

1. raz na ``DJCMS_SITES_REFRESH_SECONDS`` odświeża rejestr z listy konkursów (``registry.refresh_if_due``),
2. rozstrzyga konkurs (``resolution.resolve`` – jedno zapytanie); nieznany host, który świeża lista
   z API przypisuje aktywnemu konkursowi, uzgadnia rejestr i próbuje jeszcze raz
   (``registry.refresh_for_host``); dalej brak konkursu → **pusta** 404, zanim cokolwiek się
   wyrenderuje (jak ``platform_subdomain_miss`` w aplikacji głównej – nie ma marki, którą wolno
   pokazać),
3. ustawia ``request.site`` (``django.contrib.sites.Site`` – czyta ją django CMS i łatka
   ``apps.sites.patches``), ``request.competition_site`` (``CompetitionSite`` – czyta go klient API
   i rama) oraz ``request.competition_path_prefix``,
4. przy konkursie pod prefiksem zdejmuje prefiks z ``path_info`` i ustawia ``set_script_prefix`` –
   ten sam mechanizm co ``CompetitionMiddleware`` aplikacji głównej: urlconf widzi adres od korzenia
   witryny konkursu, a ``reverse()``/``get_absolute_url()`` dokładają prefiks. ``request.path``
   zostaje z prefiksem (adres, o który prosiła przeglądarka). Prefiks skryptu jest stanem wątku –
   przywracamy go w ``finally``, także po wyjątku.

Wyjątek od pustej 404 – adresy rozstrzygane **miękko**: statyki (``/djcms/static/``), healthcheck
(``/djcms/healthz/``) i media w devie (``/djcms/media/``). Nie zależą od konkursu, więc idą bez
odświeżania rejestru (healthcheck nie może pytać API i ma odpowiadać także przy pustym rejestrze)
i bez 404 dla nieznanego hosta. Dostają witrynę **leniwą** (``_soft_site``: konkursu hosta albo
zastępczą): pasek narzędzi django CMS (``ToolbarMiddleware``) czyta witrynę przy każdej odpowiedzi,
która do niego dojdzie, a plik statyczny podany przez WhiteNoise nie kosztuje przez to ani jednego
zapytania.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import DisallowedHost
from django.http import HttpResponseNotFound
from django.urls import get_script_prefix, set_script_prefix
from django.utils.functional import SimpleLazyObject

from . import registry
from .models import CompetitionSite
from .resolution import LOCAL_HOSTS, Resolution, normalise_host, resolve

#: Adres healthchecka compose'a – rozstrzygany miękko (patrz docstring modułu).
HEALTHZ_PATH = "/djcms/healthz/"


def _soft_prefixes() -> tuple[str, ...]:
    return (settings.STATIC_URL, HEALTHZ_PATH, settings.MEDIA_URL)


def _soft_site(host: str) -> Site | None:
    """Witryna żądania rozstrzyganego miękko: konkursu hosta, domyślnego, a bez rejestru – pierwsza."""
    resolution = resolve(host, "/") if host else Resolution(None)
    if resolution.site is not None:
        return resolution.site.site
    default = CompetitionSite.objects.filter(is_default=True).select_related("site").first()
    return default.site if default is not None else Site.objects.order_by("pk").first()


def _host(request) -> str:
    try:
        return normalise_host(request.get_host())
    except DisallowedHost:
        # Odmowę ``ALLOWED_HOSTS`` składa Django (400) – tu nie ma czego rozstrzygać.
        return ""


class CompetitionSiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith(_soft_prefixes()):
            # Witryna **leniwa**: plik statyczny, który WhiteNoise znajdzie, nie kosztuje zapytania;
            # sięga po nią dopiero pasek narzędzi django CMS (healthcheck, brakujący plik → 404 CMS).
            host = _host(request)
            request.competition_site = None
            request.competition_path_prefix = ""
            request.site = SimpleLazyObject(lambda: _soft_site(host))
            return self.get_response(request)
        previous_script_prefix = get_script_prefix()
        try:
            resolution = self._resolve(request)
            if resolution.site is None:
                return HttpResponseNotFound()
            self._apply(request, resolution)
            return self.get_response(request)
        finally:
            set_script_prefix(previous_script_prefix)

    @staticmethod
    def _resolve(request) -> Resolution:
        host = _host(request)
        if not host:
            return Resolution(None)
        registry.refresh_if_due(request=request)
        resolution = resolve(host, request.path_info)
        if resolution.site is None and host not in LOCAL_HOSTS:
            if registry.refresh_for_host(host, request=request):
                resolution = resolve(host, request.path_info)
        return resolution

    @staticmethod
    def _apply(request, resolution: Resolution) -> None:
        competition = resolution.site
        assert competition is not None  # wołane wyłącznie po rozstrzygnięciu z konkursem
        request.competition_site = competition
        request.site = competition.site
        request.competition_path_prefix = resolution.path_prefix
        if not resolution.path_prefix:
            return
        # Odcinamy **segment**, a nie stałą liczbę znaków: ``//druga/x/`` ma dać tę samą resztę.
        rest = request.path_info.lstrip("/")[len(resolution.path_prefix) :]
        request.path_info = rest if rest.startswith("/") else f"/{rest}"
        set_script_prefix(f"/{resolution.path_prefix}/")


class PrefixedCurrentPageMiddleware:
    """``request.current_page`` dla konkursu pod prefiksem ścieżki – stoi zaraz za ``CurrentPageMiddleware``.

    ``cms.utils.page.get_page_from_request`` (django CMS 5.1.3) zdejmuje z ``request.path_info``
    wynik ``reverse("pages-root")``. Pod prefiksem ``reverse`` oddaje ``/druga/``, a ``path_info``
    (po zdjęciu prefiksu przez ``CompetitionSiteMiddleware``) zaczyna się od ``/`` – korzeń nie
    zostałby zdjęty i strona bieżąca wyszłaby pusta (menu bez zaznaczenia, pasek narzędzi bez
    strony). Tu podajemy tę samą funkcję z jawną ścieżką względną do korzenia witryny; bez
    prefiksu warstwa nie robi nic.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "competition_path_prefix", ""):
            request.current_page = SimpleLazyObject(lambda: _prefixed_current_page(request))
        return self.get_response(request)


def _prefixed_current_page(request):
    from cms.utils.page import get_page_from_request

    if not hasattr(request, "_current_page_cache"):
        path = request.path_info.strip("/")
        request._current_page_cache = get_page_from_request(request, use_path=path, clean_path=False)
    return request._current_page_cache
