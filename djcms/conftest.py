"""Wspólne fixture'y testów djcms.

Klient API aplikacji głównej jest zamockowany w **każdym** teście (``main_api`` niżej, autouse).

Witryna konkursu (DJ-02d): djcms nie ma ``SITE_ID`` – każde żądanie rozstrzyga konkurs po hoście.
W każdym teście z bazą istnieje konkurs domyślny ``kwantowa`` na witrynie #1 pod hostem ``testserver``
(``competition_site``, autouse), więc klient testowy Django trafia w niego bez żadnych ustawień.
Testy wielu witryn zakładają kolejne konkursy fabryką ``make_competition``.

Strony powstają przez publiczne API django CMS (``cms.api.create_page``) i są publikowane przez
djangocms-versioning (``Version.publish``) – tak samo, jak zrobi to importer (DJ-01g). Testy nie
piszą do tabel CMS-a bezpośrednio: zmiana schematu w kolejnym wydaniu django CMS ma wywrócić
test na API, a nie przejść niezauważona przez ręcznie sklejony wiersz.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
from django.core.cache import caches

#: Konkurs z fixture'a ``competition_site`` – slug, host i adres publiczny.
DEFAULT_SLUG = "kwantowa"
DEFAULT_HOST = "testserver"
DEFAULT_ORIGIN = "https://olimpiada.example"
API_PREFIX = "/internal/djcms/v2/"

# --- API aplikacji głównej: zawsze zamockowane ----------------------------------------------------
#
# W kontenerze dev ``web:8000`` jest osiągalny, więc test, który wyrenderuje ramę bez mocka,
# zapytałby **prawdziwe** API – wynik zależałby od stanu bazy deweloperskiej. Dlatego fixture
# ``main_api`` jest autouse: domyślnie każde żądanie kończy się błędem połączenia (rama degraduje),
# a test, który potrzebuje danych, ustawia je przez ``main_api.set("chrome", {...})`` – endpoint
# konkursu ``kwantowa`` (``c/kwantowa/chrome``); inny konkurs: ``competition="fizyka"``, lista
# konkursów: ``main_api.set("competitions", {...}, competition=None)``.


class FakeResponse(io.BytesIO):
    """Odpowiedź ``urllib`` na tyle, na ile czyta ją ``apps.live.client``: ``status``, ``read``, ``with``."""

    def __init__(self, body: bytes, status: int = 200, on_read=None):
        super().__init__(body)
        self.status = status
        self._on_read = on_read

    def read(self, size=-1):
        if self._on_read is not None:
            self._on_read()
        return super().read(size)


def api_path(endpoint: str, competition: str | None = DEFAULT_SLUG) -> str:
    """Ścieżka endpointu względem ``/internal/djcms/v2/``: ``c/<slug>/<endpoint>`` albo ``competitions``."""
    return endpoint if competition is None else f"c/{competition}/{endpoint}"


def default_competitions_payload() -> dict:
    """``GET competitions`` świata testów: jeden konkurs – domyślny ``kwantowa`` pod ``testserver``.

    Ten sam konkurs, który zakłada ``competition_site``; odpowiedź jest ustawiona zawsze, bo pyta
    o nią także ``download_export`` bez konkursu (``platform.default_slug``) i ``default_site()``.
    """
    return {
        "platform": {
            "site_domain": "olimpiada.example",
            "platform_subdomains": False,
            "default_slug": DEFAULT_SLUG,
        },
        "competitions": [
            {
                "slug": DEFAULT_SLUG,
                "name": "Olimpiada Kwantowa",
                "short_name": "OK",
                "is_active": True,
                "is_default": True,
                "routing_mode": "DOMAIN",
                "path_prefix": "",
                "hosts": [DEFAULT_HOST],
                "hosts_path_prefixes": False,
                "public_base": {"origin": DEFAULT_ORIGIN, "path_prefix": ""},
                "site_hostname": "example.com",
                "has_site_aliases": False,
                "linked_paths": [],
                "fingerprint": "f" * 64,
            }
        ],
    }


class FakeOpener:
    """Otwieracz podstawiany za ``apps.live.client._build_opener``. Zapisuje każde żądanie."""

    def __init__(self):
        self.responses: dict[str, object] = {}
        self.requests: list = []
        self.timeouts: list[float] = []
        self.set("competitions", default_competitions_payload(), competition=None)

    def set(
        self,
        endpoint: str,
        payload: dict | None = None,
        *,
        competition: str | None = DEFAULT_SLUG,
        status: int = 200,
        raw: bytes | None = None,
        on_read=None,
    ):
        body = raw if raw is not None else json.dumps({"api_version": 2, **(payload or {})}).encode()
        self.responses[api_path(endpoint, competition)] = lambda: FakeResponse(body, status, on_read)

    def fail(self, endpoint: str, exc: BaseException, *, competition: str | None = DEFAULT_SLUG):
        def _raise():
            raise exc

        self.responses[api_path(endpoint, competition)] = _raise

    def calls(self, endpoint: str, *, competition: str | None = DEFAULT_SLUG) -> int:
        suffix = API_PREFIX + api_path(endpoint, competition)
        return sum(1 for request in self.requests if request.full_url.endswith(suffix))

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        endpoint = request.full_url.split(API_PREFIX, 1)[-1]
        factory = self.responses.get(endpoint)
        if factory is None:
            raise urllib.error.URLError(ConnectionRefusedError("testy: brak API"))
        return factory()


@pytest.fixture(autouse=True)
def main_api(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr("apps.live.client._build_opener", lambda: opener)
    return opener


@pytest.fixture(autouse=True)
def _clear_caches():
    """Bufory nie przenoszą stanu między testami (licznik blokady logowania jest w bazie testowej)."""
    for cache in caches.all():
        cache.clear()
    yield
    for cache in caches.all():
        cache.clear()


@pytest.fixture
def editor(django_user_model):
    """Konto personelu (``is_staff``) – dostaje luźną politykę CSP i pasek narzędzi."""
    return django_user_model.objects.create_user(
        username="redaktor@example.com",
        email="redaktor@example.com",
        password="haslo-redaktora-123",
        is_staff=True,
    )


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin@example.com", email="admin@example.com", password="haslo-admina-123456"
    )


def competitions_entry(site, **overrides) -> dict:
    """Wpis ``GET competitions`` (kontrakt v2, § 4.2) opisujący konkurs ``site`` z rejestru."""
    entry = {
        "slug": site.slug,
        "name": site.name,
        "short_name": site.name[:10],
        "is_active": site.is_active,
        "is_default": site.is_default,
        "routing_mode": site.routing_mode,
        "path_prefix": site.path_prefix,
        "hosts": sorted(site.hosts.values_list("host", flat=True)),
        "hosts_path_prefixes": site.hosts_path_prefixes,
        "public_base": (
            {"origin": site.public_origin, "path_prefix": site.public_path_prefix}
            if site.public_origin
            else None
        ),
        "site_hostname": site.site.domain,
        "has_site_aliases": False,
        "linked_paths": list(site.linked_paths),
        "fingerprint": "0" * 64,
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def make_competition(db):
    """Fabryka konkursu w rejestrze: ``make_competition("fizyka", hosts=["fizyka.example"])``.

    Zakłada ``Site`` + ``CompetitionSite`` + hosty wprost (bez API) – tak, jak zostawiłoby je
    uzgodnienie rejestru (``apps.sites.registry``); jego własne testy idą przez ``sync_registry``.
    """
    from django.contrib.sites.models import Site

    from apps.sites.models import CompetitionHost, CompetitionSite

    def _make(
        slug: str,
        *,
        hosts=(),
        name: str | None = None,
        site=None,
        is_default: bool = False,
        is_active: bool = True,
        routing_mode: str = "DOMAIN",
        path_prefix: str = "",
        hosts_path_prefixes: bool = False,
        public_origin: str = "",
        public_path_prefix: str = "",
    ):
        site = site or Site.objects.create(domain=f"{slug}.djcms.invalid", name=slug)
        competition = CompetitionSite.objects.create(
            site=site,
            slug=slug,
            name=name or f"Konkurs {slug}",
            is_default=is_default,
            is_active=is_active,
            routing_mode=routing_mode,
            path_prefix=path_prefix,
            hosts_path_prefixes=hosts_path_prefixes,
            public_origin=public_origin,
            public_path_prefix=public_path_prefix,
        )
        for host in hosts:
            CompetitionHost.objects.create(host=host, competition=competition)
        return competition

    return _make


@pytest.fixture(autouse=True)
def competition_site(request):
    """Konkurs domyślny ``kwantowa`` (witryna #1, host ``testserver``) w każdym teście z bazą.

    Witryna #1 to ta, którą ``django.contrib.sites`` zakłada przy migracji – ta sama, którą
    przejmuje konkurs domyślny przy pierwszym uzgodnieniu rejestru (``registry.LEGACY_SITE_ID``).
    """
    if request.node.get_closest_marker("django_db") is None:
        return None
    request.getfixturevalue("db")
    from django.contrib.sites.models import Site

    site, _ = Site.objects.get_or_create(pk=1, defaults={"domain": "example.com", "name": "example.com"})
    make = request.getfixturevalue("make_competition")
    return make(
        DEFAULT_SLUG,
        name="Olimpiada Kwantowa",
        site=site,
        hosts=[DEFAULT_HOST],
        is_default=True,
        public_origin=DEFAULT_ORIGIN,
    )


@pytest.fixture
def make_page(superuser):
    """Tworzy stronę w korzeniu drzewa; ``publish=True`` publikuje jej wersję (versioning).

    Witryna: rodzica, podana jawnie (``site=``) albo witryna konkursu domyślnego – ``create_page``
    bez witryny i bez rodzica rzuciłby ``ImproperlyConfigured`` (brak ``SITE_ID``, DJ-02 D4).
    """
    from cms.api import create_page
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    def _make(
        title: str,
        slug: str,
        *,
        publish: bool = True,
        template: str = "dj/pages/content.html",
        parent=None,
        menu_title: str | None = None,
        in_navigation: bool = True,
        home: bool = False,
        site=None,
    ):
        from django.contrib.sites.models import Site

        page = create_page(
            title,
            template,
            "pl",
            slug=slug,
            created_by=superuser,
            in_navigation=in_navigation,
            parent=parent,
            menu_title=menu_title,
            site=site or (parent.site if parent is not None else Site.objects.get(pk=1)),
        )
        if publish:
            content = PageContent.admin_manager.get(page=page, language="pl")
            Version.objects.get_for_content(content).publish(superuser)
        if home:
            # ``set_as_homepage`` wymaga transakcji (blokada korzeni drzewa) – test ``django_db``
            # już w niej jest; importer (DJ-01g) musi o nią zadbać sam.
            page.set_as_homepage(superuser)
        return page

    return _make


def _chrome_payload(**overrides) -> dict:
    """Odpowiedź ``chrome`` w kształcie z ``backend/apps/cms/djcms_api/views.py::chrome`` (pełna rama)."""
    item = {
        "index": 0,
        "kind": "stage",
        "title": "Etap I",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "status": "current",
        "dates": "1–31 paź",
        "url": "/harmonogram/",
        "note": "",
    }
    payload = {
        "api_version": 2,
        "generated_at": "2026-09-26T12:00:00+02:00",
        "competition": {"slug": "kwantowa", "name": "Olimpiada Kwantowa"},
        "site": {
            "site_name": "Olimpiada Testowa",
            "tagline": "Hasło testowe",
            "organizer_name": "Fundacja Testowa",
            "organizer_logo": {"src": "https://s3.olimpiada.example/logo.png", "width": 240, "height": 80},
            "organizer_address": "ul. Testowa 1",
            "organizer_registry": "KRS 0000000000",
            "contact_email": "kontakt@olimpiada.example",
            "contact_phone": "+48 500 600 700",
            "contact_url": "https://olimpiada.example/kontakt",
            "social_links": [{"url": "https://facebook.com/x", "label": "Facebook", "icon": "facebook"}],
            "registration_note": "",
        },
        "edition": {
            "id": 3,
            "year_label": "I 2026/2027",
            "title": "I edycja 2026/2027",
            "title_cap": "I Edycja 2026/2027",
        },
        "registration": {
            "is_open": True,
            "reason": "open",
            "opens_at": None,
            "opens_at_display": "",
            "closes_at": None,
            "message": "",
        },
        "supervisor_registration": {"enabled": True, "url": "https://olimpiada.example/register/supervisor/"},
        "links": {
            "login": "https://olimpiada.example/login/",
            "register": "https://olimpiada.example/register/",
            "support": "https://olimpiada.example/support/new/",
            "posters": "https://olimpiada.example/plakaty/",
            "main_home": "https://olimpiada.example/",
        },
        "announcements": [
            {
                "id": 4,
                "text": "Uwaga <b>ważne</b>",
                "level": "warning",
                "link_url": "https://olimpiada.example/x",
                "link_label": "Więcej",
                "has_link": True,
                "dismissible": True,
            },
        ],
        "sponsor_slider": {
            "seconds": 5,
            "entries": [
                {
                    "name": "Sponsor A",
                    "url": "https://sponsor.example",
                    "src": "https://s3.olimpiada.example/a.png",
                    "width": 160,
                    "height": 48,
                },
            ],
        },
        "timeline_strip": {
            "edition": "I 2026/2027",
            "header_lines": ["rok_szkolny(2026, 2027)"],
            "items": [item],
            "axis_start": "2026-09-01",
            "axis_end": "2027-06-30",
            "progress": 0.1,
            "head": 3,
            "size": 4,
            "lead": item,
            "cells": [
                {
                    "index": 0,
                    "char": "=",
                    "status": "past",
                    "is_head": False,
                    "css_class": "tl__cell",
                    "items": [],
                    "label": "",
                    "url": "",
                    "indexes": "",
                },
                {
                    "index": 1,
                    "char": "|",
                    "status": "current",
                    "is_head": False,
                    "css_class": "tl__cell tl__mark",
                    "items": [item],
                    "label": "Etap I, 1–31 paź",
                    "url": "/harmonogram/",
                    "indexes": "0",
                },
                {
                    "index": 2,
                    "char": "|",
                    "status": "current",
                    "is_head": False,
                    "css_class": "tl__cell tl__mark",
                    "items": [item, item],
                    "label": "Dwa",
                    "url": "",
                    "indexes": "0,0",
                },
                {
                    "index": 3,
                    "char": ">",
                    "status": "head",
                    "is_head": True,
                    "css_class": "tl__cell tl__head",
                    "items": [],
                    "label": "",
                    "url": "",
                    "indexes": "",
                },
            ],
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def chrome_payload():
    """Fabryka odpowiedzi ``chrome``: ``chrome_payload(registration={...})`` nadpisuje klucze główne."""
    return _chrome_payload
