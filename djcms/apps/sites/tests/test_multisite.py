"""Wiele witryn w jednej bazie djcms (DJ-02 D4, § 5.1) – łatka ``SiteManager.get_current`` i jej skutki.

Miejsca django CMS 5.1.3 / Django 6.1.1, które pomijają ``request.site`` i czytają witrynę przez
``Site.objects.get_current(request)`` – każde sprawdzone tu dla hosta **i** prefiksu ścieżki:

- ``cms.plugin_rendering.BaseRenderer.current_site`` → klucz bufora placeholderów
  (``cms/cache/placeholder.py``; unieważnienie liczy się od ``page.site_id``),
- ``django.contrib.sites.shortcuts.get_current_site`` (sitemapy Django, „zobacz na stronie” admina),
- ``cms.utils.get_current_site`` (czyta ``request.site`` samo – kontrola spójności),
- menu (``menus.menu_pool.MenuRenderer.cache_key`` – witryna w kluczu).
"""

import re

import pytest
from django.contrib.sites.models import Site, SiteManager
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

from apps.sites import patches
from apps.sites.middleware import CompetitionSiteMiddleware
from apps.sites.models import CompetitionSite

pytestmark = pytest.mark.django_db


@pytest.fixture
def fizyka(make_competition):
    return make_competition("fizyka", hosts=["fizyka.example"], public_origin="https://fizyka.example")


@pytest.fixture
def druga(make_competition):
    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    return make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://testserver",
        public_path_prefix="/druga",
    )


def resolved(path: str, host: str):
    """Żądanie po przejściu warstwy witryn (``request.site`` i prefiks jak w prawdziwym łańcuchu)."""
    request = RequestFactory().get(path, HTTP_HOST=host)
    captured = {}

    def keep(req):
        from django.urls import get_script_prefix

        captured["request"], captured["prefix"] = req, get_script_prefix()
        from django.http import HttpResponse

        return HttpResponse()

    CompetitionSiteMiddleware(keep)(request)
    return captured["request"]


# --- łatka ----------------------------------------------------------------------------------------


def test_patch_is_installed_idempotently():
    assert patches.is_installed()
    before = SiteManager.get_current
    patches.install()
    assert SiteManager.get_current is before


def test_patch_refuses_a_changed_signature(monkeypatch):
    def changed(self, request=None, *, strict=False):  # pragma: no cover - tylko sygnatura
        return None

    monkeypatch.setattr(patches, "original_get_current", changed)
    with pytest.raises(ImproperlyConfigured, match="sygnaturę"):
        patches._check_signature()


def test_original_signature_is_what_the_patch_expects():
    # Upgrade Django zmieniający ``get_current`` ma wywrócić ten test, a nie cicho zmienić zachowanie.
    patches._check_signature()


def test_request_site_wins_for_host_and_prefix(fizyka, druga):
    for path, host, expected in (
        ("/", "fizyka.example", fizyka.site),
        ("/druga/zadania/", "testserver", druga.site),
        ("/zadania/", "testserver", Site.objects.get(pk=1)),
    ):
        request = resolved(path, host)
        assert Site.objects.get_current(request) == expected
        from cms.utils import get_current_site as cms_site
        from django.contrib.sites.shortcuts import get_current_site as django_site

        assert cms_site(request) == expected
        assert django_site(request) == expected


def test_request_without_site_falls_back_to_the_original_host_lookup():
    Site.objects.filter(pk=1).update(domain="testserver")
    Site.objects.clear_cache()
    request = RequestFactory().get("/", HTTP_HOST="testserver")
    assert Site.objects.get_current(request).pk == 1
    with pytest.raises(Site.DoesNotExist):
        Site.objects.get_current(RequestFactory().get("/", HTTP_HOST="fizyka.example"))


def test_call_without_request_is_a_configuration_error():
    # Bezpiecznik D4: kod poza żądaniem podaje witrynę jawnie.
    with pytest.raises(ImproperlyConfigured):
        Site.objects.get_current()


def test_create_page_without_site_and_parent_is_refused(superuser):
    from cms.api import create_page

    with pytest.raises(ImproperlyConfigured):
        create_page("Bez witryny", "dj/pages/content.html", "pl", created_by=superuser)


def test_renderer_current_site_follows_request_site(fizyka, druga):
    from cms.plugin_rendering import ContentRenderer

    assert ContentRenderer(resolved("/", "fizyka.example")).current_site == fizyka.site
    assert ContentRenderer(resolved("/druga/", "testserver")).current_site == druga.site


def test_menu_cache_key_carries_the_request_site(fizyka, druga):
    from django.contrib.auth.models import AnonymousUser
    from menus.menu_pool import menu_pool

    for path, host, site in (("/", "fizyka.example", fizyka.site), ("/druga/", "testserver", druga.site)):
        request = resolved(path, host)
        request.user = AnonymousUser()
        request.LANGUAGE_CODE = "pl"
        renderer = menu_pool.get_renderer(request)
        assert renderer.site.pk == site.pk
        # ``menu_nodes_<język>_<witryna>:public`` – każda witryna ma własne węzły menu w buforze.
        assert f"menu_nodes_pl_{site.pk}:" in renderer.cache_key


# --- strony: ta sama ścieżka w wielu witrynach -------------------------------------------------------


def _title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    assert match is not None
    return match.group(1).strip()


@pytest.fixture
def three_sites(make_page, fizyka, druga):
    kw_home = make_page("Kwantowa start", "start", home=True)
    make_page("Zadania kwantowej", "zadania")
    make_page("Tylko kwantowa", "tylko-kwantowa")
    fz_home = make_page("Fizyka start", "start", home=True, site=fizyka.site)
    make_page("Zadania fizyki", "zadania", site=fizyka.site)
    dr_home = make_page("Druga start", "start", home=True, site=druga.site)
    make_page("Zadania drugiej", "zadania", site=druga.site)
    return {"kwantowa": kw_home, "fizyka": fz_home, "druga": dr_home}


def test_same_path_is_served_from_the_site_of_the_host(client, three_sites):
    assert _title(client.get("/zadania/").content.decode()).startswith("Zadania kwantowej")
    fizyka = client.get("/zadania/", HTTP_HOST="fizyka.example")
    assert _title(fizyka.content.decode()).startswith("Zadania fizyki")
    druga = client.get("/druga/zadania/")
    assert _title(druga.content.decode()).startswith("Zadania drugiej")
    # Strona jednej witryny nie istnieje pod hostem drugiej.
    assert client.get("/tylko-kwantowa/", HTTP_HOST="fizyka.example").status_code == 404
    assert client.get("/druga/tylko-kwantowa/").status_code == 404


def test_home_pages_are_per_site(client, three_sites):
    assert _title(client.get("/").content.decode()).startswith("Kwantowa start")
    assert _title(client.get("/", HTTP_HOST="fizyka.example").content.decode()).startswith("Fizyka start")
    assert _title(client.get("/druga/").content.decode()).startswith("Druga start")


def test_menu_lists_only_the_sites_own_pages_with_the_prefix(client, three_sites):
    html = client.get("/druga/zadania/").content.decode()
    nav = re.search(r'<nav class="nav nav--cms".*?</nav>', html, re.S).group(0)
    assert re.search(r'href="/druga/zadania/"\s+aria-current="page"', nav)
    assert "tylko-kwantowa" not in nav
    fizyka_nav = client.get("/", HTTP_HOST="fizyka.example").content.decode()
    assert "tylko-kwantowa" not in fizyka_nav
    assert 'href="/zadania/"' in fizyka_nav


def test_chrome_is_fetched_for_the_competition_of_the_host(client, three_sites, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload(site={"site_name": "Serwis fizyki"}), competition="fizyka")
    main_api.set("chrome", chrome_payload(site={"site_name": "Serwis drugiej"}), competition="druga")
    assert "Serwis fizyki" in _title(client.get("/zadania/", HTTP_HOST="fizyka.example").content.decode())
    assert "Serwis drugiej" in _title(client.get("/druga/zadania/").content.decode())
    assert main_api.calls("chrome", competition="fizyka") == 1
    assert main_api.calls("chrome", competition="druga") == 1
    assert main_api.calls("chrome") == 0  # konkurs domyślny nie był odwiedzony


def test_main_links_use_the_competitions_public_base(client, three_sites):
    # Bez API rama bierze adres logowania z ``public_base`` konkursu (z prefiksem – ten sam host).
    html = client.get("/druga/zadania/").content.decode()
    assert 'href="https://testserver/druga/login/"' in html
    html = client.get("/", HTTP_HOST="fizyka.example").content.decode()
    assert 'href="https://fizyka.example/login/"' in html


def test_placeholder_cache_is_keyed_and_cleared_per_site(client, make_page, fizyka, druga, superuser):
    """``CMS_PLACEHOLDER_CACHE``: render zapisuje pod witryną żądania, edycja czyści pod ``page.site_id``.

    Bez łatki ``BaseRenderer.current_site`` pod hostem spoza ``Site.domain`` rzuciłby
    ``Site.DoesNotExist`` (500), a pod prefiksem zapisałby bufor pod witryną gospodarza – i edycja
    (czyszcząca bufor witryny strony) nigdy by go nie unieważniła.
    """
    from cms.api import add_plugin
    from cms.cache.placeholder import get_placeholder_cache
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    # Gospodarz prefiksu jako ``Site.domain`` – bez łatki render pod ``/druga/`` nie padłby, tylko
    # po cichu zapisał bufor pod witryną #1 (host), a nie witryną konkursu ``druga``.
    Site.objects.filter(pk=1).update(domain="testserver")
    Site.objects.clear_cache()
    for site, host, prefix in ((druga.site, "testserver", "/druga"), (fizyka.site, "fizyka.example", "")):
        page = make_page("Treść", "tresc", publish=False, site=site)
        content = PageContent.admin_manager.get(page=page, language="pl")
        placeholder = content.rescan_placeholders()["body"]
        text = add_plugin(placeholder, "TextPlugin", "pl", body="<p>Stara treść</p>")
        Version.objects.get_for_content(content).publish(superuser)

        assert "Stara treść" in client.get(f"{prefix}/tresc/", HTTP_HOST=host).content.decode()
        request = resolved(f"{prefix}/tresc/", host)
        assert get_placeholder_cache(placeholder, "pl", site.pk, request) is not None
        assert get_placeholder_cache(placeholder, "pl", 1, request) is None

        # Zmiana z pominięciem bufora: dopóki nikt go nie unieważni, strona pokazuje starą treść.
        type(text).objects.filter(pk=text.pk).update(body="<p>Nowa treść</p>")
        assert "Stara treść" in client.get(f"{prefix}/tresc/", HTTP_HOST=host).content.decode()
        # Unieważnienie tak, jak robi to edycja w adminie – bez witryny, czyli od ``page.site_id``.
        placeholder.clear_cache("pl")
        assert "Nowa treść" in client.get(f"{prefix}/tresc/", HTTP_HOST=host).content.decode()


def test_homepage_sections_are_per_site(make_page, fizyka):
    from apps.live import homepage

    make_page("Partnerzy K", "partnerzy", template="dj/pages/partners.html")
    make_page("Spis F", "dokumenty", template="dj/pages/document_index.html", site=fizyka.site)
    assert homepage.home_sections(Site.objects.get(pk=1))["documents_index"] is None
    assert homepage.home_sections(fizyka.site)["documents_index"]["title"] == "Spis F"
    assert homepage.home_sections(None)["documents_index"] is None


def test_django_sitemap_machinery_sees_the_request_site(three_sites, fizyka):
    """Sitemapy Django (``get_current_site`` → łatka) i ``CMSSitemap`` biorą witrynę żądania.

    djcms nie montuje ``CMSSitemap`` – ``sitemap.xml`` z adresami z ``public_base`` to własny widok
    (DJ-02f), bo ``Sitemap.get_domain`` buduje adres z ``Site.domain``, a ta nie zna prefiksu
    ścieżki. Test pilnuje tylko, że gdyby ktoś go użył, nie dostanie stron cudzej witryny.
    """
    from cms.sitemaps import CMSSitemap
    from django.contrib.sites.shortcuts import get_current_site

    request = resolved("/", "fizyka.example")
    site = get_current_site(request)
    assert site == fizyka.site
    sitemap = CMSSitemap()
    sitemap.site = site
    assert {url.page.site_id for url in sitemap.items()} == {fizyka.site_id}


def test_contrib_redirects_are_not_installed(settings):
    # ``django.contrib.redirects`` szuka przekierowań po ``get_current_site`` i ``SITE_ID`` – djcms ma
    # własne, per witryna (DJ-02f). Pilnujemy, żeby nie wróciło przypadkiem razem z inną aplikacją.
    assert "django.contrib.redirects" not in settings.INSTALLED_APPS
    assert not any("redirects" in name for name in settings.MIDDLEWARE)
    assert "django.contrib.sites.middleware.CurrentSiteMiddleware" not in settings.MIDDLEWARE
