"""Render z motywem: sloty, wstrzyknięcie w ``<head>``, CSP, podgląd, pamięć per wersja.

Konkurs bez motywu ma odpowiadać **dokładnie** jak przed motywami – te testy pilnują tego od
strony motywów (zero zapytań do tabel ``themes_*``, polityka CSP bez zmian), a testy złote
i budżety zapytań w ``apps/tenancy/tests`` – od strony całego serwisu.
"""

from __future__ import annotations

import json
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.themes import services
from apps.themes.models import ThemeVersion
from apps.themes.rendering import forget_engines
from apps.themes.runtime import PREVIEW_PARAM, forget_runtime, make_preview_token
from apps.web.middleware import build_policy

from .helpers import IQO_ZIP, example_files, zip_with

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_caches():
    forget_runtime()
    forget_engines()
    yield
    forget_runtime()
    forget_engines()


@pytest.fixture
def example(competition):
    version, result = services.install_package(zip_with())
    assert result.errors == []
    return version


@pytest.fixture
def themed(competition, example):
    services.activate(competition, example, {"layouts": {"header": "split"}})
    competition.refresh_from_db()
    return competition


def _csp(response) -> dict[str, str]:
    return {
        part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else ""
        for part in (p.strip() for p in response["Content-Security-Policy"].split(";"))
    }


def test_competition_without_theme_touches_no_theme_table(client_for, competition):
    with CaptureQueriesContext(connection) as queries:
        response = client_for(competition).get("/")
    assert response.status_code == 200
    assert not [q["sql"] for q in queries.captured_queries if "themes_" in q["sql"]]
    html = response.content.decode()
    assert "data-theme=" not in html and "tokens.css" not in html and "theme-preview-bar" not in html
    assert '<meta name="theme-color" content="#12233f" media="(prefers-color-scheme: light)">' in html


def test_csp_without_theme_is_the_old_policy(client_for, competition, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "https://s3.example.test"
    response = client_for(competition).get("/")
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    assert response["Content-Security-Policy"] == build_policy(nonce)


def test_themed_page_links_theme_after_app_css(client_for, themed, example):
    html = client_for(themed).get("/").content.decode()
    app_css = html.index("css/app.css")
    tokens = html.index(example.public_prefix + "tokens.css")
    theme_css = html.index(example.public_prefix + "theme.css")
    assert app_css < tokens < theme_css
    # Bez stylu inline: tokeny i arkusz wyłącznie przez <link>.
    assert "<style" not in html
    assert 'data-theme="example"' in html and 'data-color-scheme="auto"' in html
    assert 'data-layout-header="split"' in html and 'data-layout-cards="outline"' in html
    # Kolor paska przeglądarki z motywu zamiast dwóch metaznaczników classic.
    assert '<meta name="theme-color" content="#3c4bff">' in html
    assert "#12233f" not in html


def test_themed_page_uses_package_slots_and_app_defaults(client_for, themed):
    html = client_for(themed).get("/").content.decode()
    # Stopka z paczki (z dołączonym fragmentem paczki i fragmentem aplikacji).
    assert 'data-theme-slot="footer"' in html and "Przykład 1.0.0" in html
    assert 'class="footer__links"' in html
    assert '<footer class="footer">' not in html
    # Opakowanie treści z paczki – wewnątrz <main>, który zostaje własnością aplikacji.
    assert re.search(
        r'<main class="page" id="tresc"><div class="example-page" data-theme-slot="page_wrapper">', html
    )
    # Nagłówek bez nadpisania – domyślny slot aplikacji (pasek konta + menu).
    assert 'class="topbar topbar--account"' in html and "skip-link" in html


def test_scripts_keep_nonce_with_theme(client_for, themed):
    response = client_for(themed).get("/")
    html = response.content.decode()
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts and all(f'nonce="{nonce}"' in tag for tag in scripts)


def test_csp_with_theme_adds_storage_to_style_and_font_only(client_for, themed, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "https://s3.example.test"
    response = client_for(themed).get("/")
    policy = _csp(response)
    assert "https://s3.example.test" in policy["style-src"]
    assert "https://s3.example.test" in policy["font-src"]
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    base = {
        part.split(" ", 1)[0]: part.split(" ", 1)[1]
        for part in build_policy(nonce).split("; ")
        if " " in part
    }
    for directive in ("script-src", "img-src", "connect-src", "default-src", "object-src", "frame-src"):
        assert policy[directive] == base[directive], directive


def test_runtime_is_cached_per_version(client_for, themed):
    client = client_for(themed)
    client.get("/")
    with CaptureQueriesContext(connection) as queries:
        assert client.get("/").status_code == 200
    assert not [q["sql"] for q in queries.captured_queries if "themes_" in q["sql"]]


def test_panels_inherit_tokens_but_keep_their_templates(client_for, themed):
    participant = ParticipantFactory(competition=themed)
    client = client_for(themed)
    client.force_login(participant.user)
    html = client.get("/me/").content.decode()
    assert "tokens.css" in html and "theme.css" in html
    # Szablony slotów paczki obowiązują wyłącznie na stronach publicznych (§ 0, przegląd H2).
    assert "data-theme-slot" not in html
    assert '<footer class="footer">' in html and 'class="topbar topbar--account"' in html


def test_broken_theme_template_falls_back_to_default(client_for, themed, example):
    ThemeVersion.objects.filter(pk=example.pk).update(
        templates={
            **example.templates,
            "theme/footer.html": "<footer>{% url 'nie-ma-takiego-widoku' %}</footer>",
        }
    )
    forget_runtime()
    forget_engines()
    response = client_for(themed).get("/")
    assert response.status_code == 200
    assert '<footer class="footer">' in response.content.decode()


def test_loader_relints_templates_from_database(client_for, themed, example):
    """Szablon, który ominąłby walidację wgrania (zapis wprost do bazy), i tak się nie załaduje."""
    ThemeVersion.objects.filter(pk=example.pk).update(
        templates={**example.templates, "theme/footer.html": "<footer><script>alert(1)</script></footer>"}
    )
    forget_runtime()
    forget_engines()
    html = client_for(themed).get("/").content.decode()
    assert "alert(1)" not in html
    assert '<footer class="footer">' in html


# --- podgląd -------------------------------------------------------------------------------------


def _coordinator_client(client_for, competition):
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    return client


def test_preview_for_coordinator_only(client_for, competition, example):
    token = make_preview_token(competition, example.pk, {"layouts": {"cards": "flat"}})
    coordinator = _coordinator_client(client_for, competition)
    html = coordinator.get(f"/?{PREVIEW_PARAM}={token}").content.decode()
    assert 'data-theme="example"' in html and 'data-layout-cards="flat"' in html
    assert "theme-preview-bar" in html
    # Ten sam adres u gościa i u uczestnika – bez motywu, bez paska.
    for client in (client_for(competition), _participant_client(client_for, competition)):
        html = client.get(f"/?{PREVIEW_PARAM}={token}").content.decode()
        assert "data-theme=" not in html and "theme-preview-bar" not in html
    # Podgląd niczego nie zapisuje.
    competition.refresh_from_db()
    assert competition.theme_version_id is None


def _participant_client(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory(competition=competition).user)
    return client


def test_preview_token_of_other_competition_is_ignored(client_for, competition, other_competition, example):
    token = make_preview_token(other_competition, example.pk, {})
    html = _coordinator_client(client_for, competition).get(f"/?{PREVIEW_PARAM}={token}").content.decode()
    assert "data-theme=" not in html


def test_tampered_preview_token_is_ignored(client_for, competition, example):
    token = make_preview_token(competition, example.pk, {})
    html = _coordinator_client(client_for, competition).get(f"/?{PREVIEW_PARAM}={token}x").content.decode()
    assert "data-theme=" not in html


def test_preview_of_classic_hides_active_theme(client_for, themed):
    token = make_preview_token(themed, None, {})
    html = _coordinator_client(client_for, themed).get(f"/?{PREVIEW_PARAM}={token}").content.decode()
    assert "data-theme=" not in html and "theme-preview-bar" in html


# --- akcent marki --------------------------------------------------------------------------------


def test_brand_accent_override_stylesheet(client_for, competition, example):
    competition.accent_colour = "#0055aa"
    competition.save(update_fields=["accent_colour"])
    services.activate(competition, example, {"brand_accent": True})
    competition.refresh_from_db()
    client = client_for(competition)
    html = client.get("/").content.decode()
    href = re.search(r'href="(/_theme/overrides\.css\?v=[^"]+)"', html).group(1)
    response = client.get(href)
    assert response.status_code == 200 and response["Content-Type"].startswith("text/css")
    css = response.content.decode()
    assert "--t-accent: #0055aa;" in css and "--t-focus" not in css
    assert "immutable" in response["Cache-Control"]
    # Inny kolor w adresie niż w konkursie – 404 (adres nie generuje arkuszy z dowolnym kolorem).
    assert client.get(href.replace("0055aa", "ff0000")).status_code == 404


# --- paczka IQO ----------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "ar"])
def test_iqo_theme_renders_home_and_panel(client_for, competition, monkeypatch, language):
    monkeypatch.setenv("APP_VERSION", "v0.41.0")
    version, result = services.install_package(IQO_ZIP.read_bytes())
    assert result.errors == []
    competition.interface_languages = ["en", "ar"]
    competition.save(update_fields=["interface_languages"])
    services.activate(competition, version)
    competition.refresh_from_db()
    client = client_for(competition)
    client.cookies["django_language"] = language
    response = client.get("/", HTTP_ACCEPT_LANGUAGE=language)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-theme="iqo-quantum"' in html and 'data-color-scheme="dark"' in html
    assert 'class="iqo-header"' in html and "topbar--account" not in html
    assert "iqo-footer" in html
    expected_dir = "rtl" if language == "ar" else "ltr"
    assert f'dir="{expected_dir}"' in html
    participant = ParticipantFactory(competition=competition)
    client.force_login(participant.user)
    assert client.get("/me/").status_code == 200


def test_manifest_layouts_documented_in_example():
    manifest = json.loads(example_files()["manifest.json"])
    assert manifest["layouts"]["header"].startswith("minimal")
