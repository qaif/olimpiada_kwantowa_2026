"""Wspólne wektory rozstrzygania host + ścieżka → konkurs (``backend/djcms_contract/resolution_cases.json``).

djcms rozstrzyga konkurs **sam** (``djcms/apps/sites/resolution.py``), z listy
``GET /internal/djcms/v2/competitions``, algorytmem-lustrem ``resolve_for_request`` (DJ-02 § 2.1,
D11). Rozjazd dwóch algorytmów znaczyłby, że strona publiczna (djcms) i adresy aplikacji (``web``)
pod tym samym hostem należą do dwóch różnych konkursów – czyli wyciek między konkursami. Ten test
jest połową kontraktu po stronie autorytatywnej:

1. buduje każdy świat z pliku w bazie (witryny Wagtaila, konkursy, bramki ``path_prefix_routing``),
2. sprawdza, że **lista konkursów z API v2** dla tego świata ma dokładnie pola z pliku – plik nie
   może opisywać świata, którego API nigdy by nie oddało,
3. puszcza każdy przypadek przez to, co w ``web`` naprawdę odpowiada na żądanie: ``ALLOWED_HOSTS``
   (``DisallowedHost`` = brak konkursu), ``resolve_for_request`` i ``platform_subdomain_miss``.

Druga połowa (djcms) uruchamia te same przypadki przez swój ``resolve``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory
from wagtail.models import Page, Site

from apps.cms.djcms_api.competitions import competitions_payload
from apps.tenancy.models import Competition
from apps.tenancy.resolution import PATH_PREFIX_FLAG, platform_subdomain_miss, resolve_for_request

pytestmark = pytest.mark.django_db

CASES_FILE = Path(django_settings.BASE_DIR) / "djcms_contract" / "resolution_cases.json"
CONTRACT = json.loads(CASES_FILE.read_text(encoding="utf-8"))

#: Pola konkursu, które wektory biorą z listy ``competitions`` API v2 (reszta – adresy publiczne,
#: ``linked_paths``, ``fingerprint`` – do rozstrzygania nie służy).
WORLD_FIELDS = (
    "slug",
    "is_active",
    "is_default",
    "routing_mode",
    "path_prefix",
    "hosts",
    "hosts_path_prefixes",
    "site_hostname",
)


def _worlds():
    return [pytest.param(world, id=world["id"]) for world in CONTRACT["worlds"]]


def _cases():
    return [
        pytest.param(world, case, id=f"{world['id']}:{case['id']}")
        for world in CONTRACT["worlds"]
        for case in world["cases"]
    ]


def build_world(world, settings, default_competition) -> None:
    """Stawia świat z pliku: konkurs domyślny to Konkurs #1 z migracji, reszta – nowe witryny.

    ``ALLOWED_HOSTS`` jak w ``config/settings/base.py``: adresy wewnętrzne, domena platformy,
    hosty konkursów spoza subdomen platformy (``EXTRA_DOMAINS``) i – przy włączonych subdomenach –
    wpis wieloznaczny ``.SITE_DOMAIN``. Subdomeny konkursów **nie** są wypisane z nazwy, bo
    w produkcji też nie są (wpuszcza je wildcard, a ``platform_subdomain_miss`` pilnuje reszty).
    """
    platform = world["platform"]
    site_domain = platform["site_domain"]
    settings.SITE_DOMAIN = site_domain
    settings.PLATFORM_SUBDOMAINS = platform["platform_subdomains"]
    settings.EXTRA_DOMAINS = []
    explicit = [
        host
        for entry in world["competitions"]
        for host in entry["hosts"]
        if not (platform["platform_subdomains"] and host.endswith(f".{site_domain}"))
    ]
    wildcard = [f".{site_domain}"] if platform["platform_subdomains"] else []
    settings.ALLOWED_HOSTS = list(
        dict.fromkeys(["localhost", "127.0.0.1", "web", site_domain, *explicit, *wildcard])
    )

    root = Page.objects.filter(depth=1).order_by("path").first()
    for entry in world["competitions"]:
        flags = {PATH_PREFIX_FLAG: entry["hosts_path_prefixes"]}
        values = {
            "is_active": entry["is_active"],
            "routing_mode": entry["routing_mode"],
            "path_prefix": entry["path_prefix"],
            "primary_domain": entry["site_hostname"],
        }
        if entry["is_default"]:
            Site.objects.filter(pk=default_competition.site_id).update(
                hostname=entry["site_hostname"], port=80
            )
            Competition.objects.filter(pk=default_competition.pk).update(
                slug=entry["slug"],
                feature_flags={**(default_competition.feature_flags or {}), **flags},
                **values,
            )
            continue
        home = root.add_child(instance=Page(title=entry["slug"], slug=f"swiat-{entry['slug']}"))
        site = Site.objects.create(
            hostname=entry["site_hostname"], port=80, root_page=home, site_name=entry["slug"]
        )
        Competition.objects.create(
            site=site,
            slug=entry["slug"],
            name=f"Olimpiada {entry['slug']}",
            organizer_name="Organizator testowy",
            feature_flags=flags,
            **values,
        )
    # Wagtail zapamiętuje korzenie witryn w buforze – świat zmienił się pod nim.
    cache.clear()


def resolve_like_web(host: str, path: str) -> dict:
    """Konkurs, który ``web`` naprawdę obsłużyłby pod tym hostem i ścieżką (patrz docstring modułu)."""
    request = RequestFactory().get(path, HTTP_HOST=host)
    try:
        request.get_host()
    except DisallowedHost:
        return {"slug": None, "path_prefix": ""}
    resolution = resolve_for_request(request)
    competition = resolution.competition
    if competition is None or platform_subdomain_miss(request, competition):
        return {"slug": None, "path_prefix": ""}
    return {"slug": competition.slug, "path_prefix": resolution.path_prefix}


def test_the_contract_file_has_a_known_version_and_unique_case_ids():
    assert CONTRACT["version"] == 1
    for world in CONTRACT["worlds"]:
        ids = [case["id"] for case in world["cases"]]
        assert len(ids) == len(set(ids)), world["id"]
        assert sum(entry["is_default"] for entry in world["competitions"]) == 1, world["id"]


@pytest.mark.parametrize("world", _worlds())
def test_the_world_is_exactly_what_the_v2_competitions_list_returns(settings, competition, world):
    build_world(world, settings, competition)

    payload = competitions_payload()

    assert payload["platform"]["site_domain"] == world["platform"]["site_domain"]
    assert payload["platform"]["platform_subdomains"] == world["platform"]["platform_subdomains"]
    assert payload["platform"]["default_slug"] == world["platform"]["default_slug"]
    listed = {
        entry["slug"]: {field: entry[field] for field in WORLD_FIELDS} for entry in payload["competitions"]
    }
    assert listed == {
        entry["slug"]: {field: entry[field] for field in WORLD_FIELDS} for entry in world["competitions"]
    }


@pytest.mark.parametrize(("world", "case"), _cases())
def test_web_resolves_every_case_as_the_contract_says(settings, competition, world, case):
    build_world(world, settings, competition)

    assert resolve_like_web(case["host"], case["path"]) == case["expect"]
