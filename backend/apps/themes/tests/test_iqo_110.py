"""Paczka IQO Quantum 1.1.0 (THEME-02 § 5): walidacja, render en/ar, nav, menu, kolory, cache, kontrast."""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from apps.cms.models import ContentPage
from apps.themes import services
from apps.themes.package import validate_package
from apps.web.middleware import build_policy

from .helpers import FIXTURES

pytestmark = pytest.mark.django_db

IQO_110 = FIXTURES / "iqo-quantum-1.1.0.zip"
CUSTOM_HREF = re.compile(r'href="(/_theme/custom\.css\?s=[^"]+)"')


@pytest.fixture
def iqo(competition, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v0.43.0")
    version, result = services.install_package(IQO_110.read_bytes())
    assert result.errors == [], result.errors
    competition.interface_languages = ["en", "ar"]
    competition.default_language = "en"
    competition.save(update_fields=["interface_languages", "default_language"])
    services.activate(competition, version)
    competition.refresh_from_db()
    return version


@pytest.fixture
def content_page(competition):
    home = competition.site.root_page.specific
    return home.add_child(instance=ContentPage(title="Rules of the olympiad", slug="rules-theme02"))


def _client(client_for, competition, language):
    client = client_for(competition)
    client.cookies["django_language"] = language
    return client


def test_package_validates_without_errors():
    result = validate_package(IQO_110.read_bytes(), app_version="v0.43.0")
    assert result.errors == []
    assert result.manifest["version"] == "1.1.0" and result.manifest["color_scheme"] == "dark"
    assert {"theme/header.html", "theme/nav.html", "theme/footer.html", "theme/home_hero.html"} <= set(
        result.templates
    )
    assert [entry["id"] for entry in result.manifest["logos"]] == ["lockup", "mark", "full"]
    assert len(result.manifest["fonts"]) >= 2
    assert result.tokens.light and result.tokens.dark
    # Nic spoza paczki: każdy url() w arkuszu wskazuje assets/ tej paczki.
    assert all(url.startswith("assets/") for url in re.findall(r'url\("([^"]+)"\)', result.theme_css))
    # Wysoki kontrast aplikacji wygrywa z paletą motywu (żółć na czerni).
    assert ':root[data-contrast="high"]' in result.theme_css and "#ffe500" in result.theme_css


def test_zip_contains_only_package_files():
    names = zipfile.ZipFile(io.BytesIO(IQO_110.read_bytes())).namelist()
    assert not [
        n for n in names if n.startswith(("_preview/", "tools/", "dist/")) or n.endswith((".py", ".md"))
    ]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_home_and_content_page_render(client_for, competition, iqo, content_page, language):
    client = _client(client_for, competition, language)
    for url in ("/", content_page.url):
        response = client.get(url, HTTP_ACCEPT_LANGUAGE=language)
        assert response.status_code == 200, url
        html = response.content.decode()
        assert 'data-theme="iqo-quantum"' in html and 'data-color-scheme="dark"' in html
        assert 'data-layout-header="split"' in html
        assert 'class="iqo-header"' in html and "topbar--account" not in html
        # Menu rysuje slot nav paczki (jedna lista), stopka paczki.
        assert 'class="nav nav--cms iqo-nav"' in html and "iqo-footer" in html
        assert f'dir="{"rtl" if language == "ar" else "ltr"}"' in html
    page = client.get(content_page.url, HTTP_ACCEPT_LANGUAGE=language).content.decode()
    assert "iqo-page-head" in page and "Rules of the olympiad" in page


def test_menu_overrides_reach_iqo_nav(client_for, competition, iqo):
    client = _client(client_for, competition, "en")
    default = client.get("/").context["cms_menu"]
    auto = {
        item["key"]: {"home": item.get("home", False), "has_children": bool(item["children"])}
        for item in default
    }
    services.save_menu(
        competition,
        [
            {
                "key": "link-0000abcd",
                "type": "link",
                "url": "https://iqo-official.org/",
                "new_tab": True,
                "labels": {"en": "Official site", "ar": "الموقع الرسمي"},
            },
            {"key": "home", "type": "auto", "labels": {"en": "Start"}},
        ],
        auto_keys=auto,
        default_keys=[item["key"] for item in default],
    )
    competition.refresh_from_db()
    nav = re.search(
        r'<nav class="nav nav--cms iqo-nav".*?</nav>', client.get("/").content.decode(), re.S
    ).group(0)
    assert nav.index("Official site") < nav.index("Start")
    assert (
        'class="nav__link iqo-ext" href="https://iqo-official.org/" target="_blank" rel="noopener noreferrer"'
        in nav
    )
    arabic = _client(client_for, competition, "ar").get("/", HTTP_ACCEPT_LANGUAGE="ar").content.decode()
    assert "الموقع الرسمي" in arabic


def test_custom_colours_scheme_logo_and_radius(client_for, competition, iqo):
    services.save_customization(
        competition,
        iqo,
        {
            "scheme": "light",
            "logo": "mark",
            "font": "technical",
            "colors": {"light": {"cta": "#2a2fb0", "cover": "#101030"}},
            "radius": {"radius-leaf": "0.5rem"},
        },
    )
    competition.refresh_from_db()
    client = _client(client_for, competition, "en")
    html = client.get("/").content.decode()
    assert 'data-color-scheme="light"' in html
    assert "assets/logo/mark.svg" in html and "iqo-brand--mark" in html
    css = client.get(CUSTOM_HREF.search(html).group(1)).content.decode()
    assert "color-scheme: light;" in css and "--t-cta: #2a2fb0;" in css and "--t-cover: #101030;" in css
    assert "--t-radius-leaf: 0.5rem;" in css and '--t-font-display: "Space Mono"' in css


def test_cover_text_contrast_is_checked(competition, iqo):
    from apps.themes.customize import CustomizationError

    with pytest.raises(CustomizationError) as excinfo:
        services.save_customization(competition, iqo, {"colors": {"dark": {"cover": "#e0e0f0"}}})
    assert any("cover-text" in message for message in excinfo.value.messages)


@pytest.mark.parametrize("value", ["99px", "4rem", "calc(1px)", "1em", "-1px"])
def test_radius_values_are_bounded(competition, iqo, value):
    cleaned = services.save_customization(competition, iqo, {"radius": {"radius-leaf": value}})
    assert "radius" not in cleaned


def test_page_cache_hit_keeps_theme_and_csp(client_for, competition, iqo, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "https://s3.example.test"
    settings.PAGE_CACHE_ENABLED = True
    services.save_customization(competition, iqo, {"colors": {"dark": {"cta": "#8c96ff"}}})
    competition.refresh_from_db()
    client = _client(client_for, competition, "en")
    miss, hit = client.get("/"), client.get("/")
    assert miss["X-Page-Cache"] == "MISS" and hit["X-Page-Cache"] == "HIT"
    for response in (miss, hit):
        html = response.content.decode()
        assert 'data-theme="iqo-quantum"' in html and "/_theme/custom.css?s=" in html
        nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
        assert response["Content-Security-Policy"] == build_policy(nonce, theme_assets=True)


def test_high_contrast_toggle_and_back_restores_theme(client_for, competition, iqo):
    client = _client(client_for, competition, "en")
    client.post("/account/preferences/", {"language": "en", "high_contrast": "1", "next": "/"})
    high = client.get("/").content.decode()
    assert 'data-contrast="high"' in high and 'data-theme="iqo-quantum"' in high
    client.post("/account/preferences/", {"language": "en", "high_contrast": "0", "next": "/"})
    back = client.get("/").content.decode()
    assert 'data-contrast="high"' not in back
    assert 'data-theme="iqo-quantum"' in back and iqo.public_prefix + "theme.css" in back


def test_sponsor_slider_reaches_package_footer_only_with_entries(client_for, competition, iqo):
    html = _client(client_for, competition, "en").get("/").content.decode()
    # Bez wpisów taśmy stopka paczki nie rysuje pustego pasa partnerów.
    assert "iqo-footer__partners" not in html
