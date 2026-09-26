"""SEO w trybie ``primary`` (DJ-02 § 8): canonical, ``og:*``, description, GA4 + CSP, robots, sitemap.

Adresy bezwzględne wyłącznie z ``public_base`` konkursu (``public_origin`` + ``public_path_prefix``),
nigdy z ``Site.domain`` ani z nagłówka ``Host``. Konkurs z fixture'a: ``kwantowa`` pod ``testserver``
z adresem publicznym ``https://olimpiada.example``.
"""

import re

import pytest
from django.contrib.sites.models import Site

from apps.pages import seo

PROXY = "172.30.1.5"
PRIMARY = {"HTTP_X_DJCMS_MODE": "primary", "REMOTE_ADDR": PROXY}
GA_ID = "G-TEST123456"


def _directives(response) -> dict[str, list[str]]:
    result = {}
    for part in response["Content-Security-Policy"].split(";"):
        name, *values = part.split()
        result[name] = values
    return result


@pytest.fixture
def druga(make_competition, competition_site):
    """Konkurs pod prefiksem ``/druga/`` na hoście konkursu domyślnego (jak ``path_prefix_routing``)."""
    competition_site.hosts_path_prefixes = True
    competition_site.save(update_fields=["hosts_path_prefixes"])
    return make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://olimpiada.example",
        public_path_prefix="/druga",
    )


# --- canonical i og:url ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("meta", [{}, PRIMARY])
def test_canonical_and_og_url_from_public_base_without_query(client, make_page, meta):
    make_page("O olimpiadzie", "o-olimpiadzie")
    html = client.get("/o-olimpiadzie/?utm_source=x", **meta).content.decode()
    assert '<link rel="canonical" href="https://olimpiada.example/o-olimpiadzie/">' in html
    assert '<meta property="og:url" content="https://olimpiada.example/o-olimpiadzie/">' in html
    assert "utm_source" not in html.split("</head>")[0]


@pytest.mark.django_db
def test_canonical_of_nested_page_and_home(client, make_page):
    home = make_page("Start", "start", home=True)
    make_page("Regulamin", "regulamin", parent=make_page("Dokumenty", "dokumenty"))
    assert home.is_home
    html = client.get("/", **PRIMARY).content.decode()
    assert '<link rel="canonical" href="https://olimpiada.example/">' in html
    html = client.get("/dokumenty/regulamin/", **PRIMARY).content.decode()
    assert '<link rel="canonical" href="https://olimpiada.example/dokumenty/regulamin/">' in html


@pytest.mark.django_db
def test_canonical_ignores_host_header_and_site_domain(client, make_page, competition_site):
    competition_site.hosts.create(host="alias.olimpiada.example")
    make_page("O olimpiadzie", "o-olimpiadzie")
    html = client.get("/o-olimpiadzie/", HTTP_HOST="alias.olimpiada.example", **PRIMARY).content.decode()
    assert '<link rel="canonical" href="https://olimpiada.example/o-olimpiadzie/">' in html
    assert "alias.olimpiada.example" not in html.split("</head>")[0]
    assert Site.objects.get(pk=1).domain not in html.split("</head>")[0]


@pytest.mark.django_db
def test_canonical_under_path_prefix(client, make_page, druga):
    make_page("Zadania drugiej", "zadania", site=druga.site)
    response = client.get("/druga/zadania/", **PRIMARY)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<link rel="canonical" href="https://olimpiada.example/druga/zadania/">' in html
    # Rama: adresy od korzenia konkursu, nie od korzenia hosta (tam jest inny konkurs).
    assert '<a class="brand" href="/druga/">' in html
    assert '<a href="/druga/dokumenty/rodo/">' in html


@pytest.mark.django_db
def test_no_canonical_without_public_base(client, make_page, competition_site):
    competition_site.public_origin = ""
    competition_site.save(update_fields=["public_origin"])
    make_page("O olimpiadzie", "o-olimpiadzie")
    html = client.get("/o-olimpiadzie/", **PRIMARY).content.decode()
    assert 'rel="canonical"' not in html
    assert 'property="og:url"' not in html


# --- parytet <head> z Wagtailem (backend/templates/base.html) --------------------------------------


@pytest.mark.django_db
def test_head_parity_fields_from_chrome_seo(client, make_page, main_api, chrome_payload):
    payload = chrome_payload()
    payload["seo"] = {
        "og_image": "https://olimpiada.example/static/img/og-image.png",
        "default_description": "Opis domyślny z API.",
    }
    main_api.set("chrome", payload)
    make_page("Wyniki", "wyniki", template="dj/pages/news_index.html")
    html = client.get("/wyniki/", **PRIMARY).content.decode()
    head = html.split("</head>")[0]
    assert '<html lang="pl">' in html
    assert "<title>Wyniki – Olimpiada Testowa</title>" in head
    assert '<meta name="description" content="Opis domyślny z API.">' in head
    assert '<meta property="og:site_name" content="Olimpiada Testowa">' in head
    assert '<meta property="og:title" content="Olimpiada Testowa">' in head
    assert '<meta property="og:image" content="https://olimpiada.example/static/img/og-image.png">' in head
    assert '<meta property="og:type" content="website">' in head
    assert '<link rel="icon" href="/djcms/static/img/favicon.svg" type="image/svg+xml">' in head


@pytest.mark.django_db
def test_head_fallbacks_with_dead_api(client, make_page):
    make_page("Wyniki", "wyniki", template="dj/pages/news_index.html")
    head = client.get("/wyniki/", **PRIMARY).content.decode().split("</head>")[0]
    assert f'<meta name="description" content="{seo.FALLBACK_DESCRIPTION}">' in head
    # Zapasowy og:image – własne statyki pod originem konkursu (nie z nagłówka Host).
    assert (
        '<meta property="og:image" content="https://olimpiada.example/djcms/static/img/og-image.png">' in head
    )


@pytest.mark.django_db
def test_content_page_description_like_wagtail(client, make_page, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    make_page("O olimpiadzie", "o-olimpiadzie")
    head = client.get("/o-olimpiadzie/", **PRIMARY).content.decode().split("</head>")[0]
    # ``search_description|default:page.title – site_name.`` z ``cms/content_page.html``.
    assert '<meta name="description" content="O olimpiadzie – Olimpiada Testowa.">' in head


# --- Google Analytics 4 ----------------------------------------------------------------------------


@pytest.fixture
def ga_chrome(main_api, chrome_payload):
    payload = chrome_payload()
    payload["site"]["ga_measurement_id"] = GA_ID
    main_api.set("chrome", payload)
    return main_api


@pytest.mark.django_db
def test_ga_in_primary_like_wagtail_with_csp_hosts(client, make_page, ga_chrome):
    make_page("O olimpiadzie", "o-olimpiadzie")
    response = client.get("/o-olimpiadzie/", **PRIMARY)
    html = response.content.decode()
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    assert f'<html lang="pl" data-ga-id="{GA_ID}">' in html
    assert (
        f'<script async nonce="{nonce}" src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>'
        in html
    )
    assert f'<script nonce="{nonce}" src="/djcms/static/js/analytics.js"></script>' in html
    assert f'<meta name="ga-measurement-id" content="{GA_ID}">' in html
    assert 'data-cookie-consent="all"' in html and 'data-cookie-consent="necessary"' in html
    assert "data-cookie-settings" in html
    assert "data-cookie-notice-dismiss" not in html
    csp = _directives(response)
    assert csp["script-src"] == [
        "'self'",
        f"'nonce-{nonce}'",
        "https://www.googletagmanager.com",
        "'strict-dynamic'",
    ]
    assert "https://*.google-analytics.com" in csp["connect-src"]
    assert "https://*.analytics.google.com" in csp["connect-src"]
    assert "https://*.google-analytics.com" in csp["img-src"]
    assert "'unsafe-inline'" not in csp["script-src"]


@pytest.mark.django_db
def test_no_ga_in_preview(client, make_page, ga_chrome):
    make_page("O olimpiadzie", "o-olimpiadzie")
    response = client.get("/o-olimpiadzie/")
    html = response.content.decode()
    assert "googletagmanager" not in html
    assert "data-ga-id" not in html
    assert "data-cookie-notice-dismiss" in html
    assert "googletagmanager" not in response["Content-Security-Policy"]


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["", "UA-12345-1", 'G-ABC"><script>', "G-abc123456", None])
def test_no_ga_without_valid_id(client, make_page, main_api, chrome_payload, value):
    payload = chrome_payload()
    payload["site"]["ga_measurement_id"] = value
    main_api.set("chrome", payload)
    make_page("O olimpiadzie", "o-olimpiadzie")
    response = client.get("/o-olimpiadzie/", **PRIMARY)
    assert "googletagmanager" not in response.content.decode()
    assert "googletagmanager" not in response["Content-Security-Policy"]


@pytest.mark.django_db
def test_no_ga_for_staff(client, make_page, ga_chrome, editor):
    make_page("O olimpiadzie", "o-olimpiadzie")
    client.force_login(editor)
    response = client.get("/o-olimpiadzie/", **PRIMARY)
    assert "googletagmanager" not in response.content.decode()


@pytest.mark.django_db
def test_ga_hosts_only_on_the_response_with_the_tag(client, make_page, ga_chrome):
    # Plik statyczny i robots.txt w tym samym trybie – bez hostów GA (nie wczytują tagu).
    make_page("O olimpiadzie", "o-olimpiadzie")
    assert "googletagmanager" not in client.get("/robots.txt", **PRIMARY)["Content-Security-Policy"]


# --- robots.txt ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_robots_preview_disallows_everything_and_is_not_cached(client):
    response = client.get("/robots.txt")
    assert response.content.decode() == "User-agent: *\nDisallow: /\n"
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_robots_primary_disallows_private_prefixes_and_lists_sitemap(client, settings):
    response = client.get("/robots.txt", **PRIMARY)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    assert "public" in response["Cache-Control"] and "max-age=3600" in response["Cache-Control"]
    body = response.content.decode()
    lines = body.splitlines()
    assert lines[0] == "User-agent: *"
    assert "Disallow: /" not in lines
    for segment in settings.DJ_APP_ROUTES["private_prefixes"]:
        assert f"Disallow: /{segment}/" in lines
    assert "Disallow: /djcms/" in lines
    assert "Disallow: /zadania/" not in lines
    assert lines[-1] == "Sitemap: https://olimpiada.example/sitemap.xml"


@pytest.mark.django_db
def test_robots_on_platform_host_covers_path_prefix_competitions(client, druga):
    lines = client.get("/robots.txt", **PRIMARY).content.decode().splitlines()
    assert "Disallow: /admin/" in lines
    assert "Disallow: /druga/admin/" in lines
    assert "Disallow: /druga/me/" in lines
    assert "Sitemap: https://olimpiada.example/sitemap.xml" in lines
    assert "Sitemap: https://olimpiada.example/druga/sitemap.xml" in lines


@pytest.mark.django_db
def test_robots_under_prefix_describes_only_that_competition(client, druga):
    lines = client.get("/druga/robots.txt", **PRIMARY).content.decode().splitlines()
    assert "Disallow: /druga/admin/" in lines
    assert "Disallow: /admin/" not in lines
    assert lines[-1] == "Sitemap: https://olimpiada.example/druga/sitemap.xml"


@pytest.mark.django_db
def test_robots_without_public_base_has_no_sitemap_line(client, competition_site):
    competition_site.public_origin = ""
    competition_site.save(update_fields=["public_origin"])
    body = client.get("/robots.txt", **PRIMARY).content.decode()
    assert "Sitemap:" not in body
    assert "Disallow: /admin/" in body


# --- sitemap.xml -----------------------------------------------------------------------------------


def _locs(response) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", response.content.decode())


@pytest.mark.django_db
def test_sitemap_is_404_in_preview(client, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    assert client.get("/sitemap.xml").status_code == 404


@pytest.mark.django_db
def test_sitemap_lists_published_public_pages_of_the_site(client, make_page, make_competition):
    make_page("Start", "start", home=True)
    docs = make_page("Dokumenty", "dokumenty")
    make_page("Regulamin", "regulamin", parent=docs)
    make_page("Szkic", "szkic", publish=False)
    hidden = make_page("Tylko dla zalogowanych", "ukryta")
    hidden.login_required = True
    hidden.save()
    other = make_competition("fizyka", hosts=["fizyka.example"], public_origin="https://fizyka.example")
    make_page("Obca", "obca", site=other.site)

    response = client.get("/sitemap.xml", **PRIMARY)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/xml; charset=utf-8"
    assert "public" in response["Cache-Control"] and "max-age=3600" in response["Cache-Control"]
    body = response.content.decode()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    assert sorted(_locs(response)) == [
        "https://olimpiada.example/",
        "https://olimpiada.example/dokumenty/",
        "https://olimpiada.example/dokumenty/regulamin/",
    ]
    assert re.search(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", body)

    other_response = client.get("/sitemap.xml", HTTP_HOST="fizyka.example", **PRIMARY)
    assert _locs(other_response) == ["https://fizyka.example/obca/"]


@pytest.mark.django_db
def test_sitemap_under_path_prefix(client, make_page, druga):
    make_page("Zadania drugiej", "zadania", site=druga.site)
    make_page("Zadania pierwszej", "zadania-glowne")
    response = client.get("/druga/sitemap.xml", **PRIMARY)
    assert _locs(response) == ["https://olimpiada.example/druga/zadania/"]


@pytest.mark.django_db
def test_sitemap_skips_pages_under_app_paths(make_page, competition_site, monkeypatch):
    make_page("O olimpiadzie", "o-olimpiadzie")
    monkeypatch.setattr(
        seo, "path_collides_with_app", lambda path: "kolizja" if path == "o-olimpiadzie" else None
    )
    assert seo.sitemap_entries(competition_site) == []


@pytest.mark.django_db
def test_sitemap_is_404_without_public_base(client, make_page, competition_site):
    competition_site.public_origin = ""
    competition_site.save(update_fields=["public_origin"])
    make_page("O olimpiadzie", "o-olimpiadzie")
    assert client.get("/sitemap.xml", **PRIMARY).status_code == 404


def test_sitemap_xml_escapes_locations():
    body = seo.sitemap_xml([seo.SitemapEntry("https://x.example/a&b/", None)])
    assert "<loc>https://x.example/a&amp;b/</loc>" in body
    assert "<lastmod>" not in body
