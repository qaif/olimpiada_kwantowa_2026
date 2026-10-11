"""Dostosowanie motywu (THEME-02 § 2): kolory, kontrast, schemat, logo, kroje, arkusz ``custom.css``."""

from __future__ import annotations

import json
import re

import pytest

from apps.core.models import AuditLog
from apps.themes import customize, services
from apps.themes.models import ThemeCustomization
from apps.themes.runtime import runtime_for
from apps.web.middleware import build_policy

from .helpers import example_files, zip_with

pytestmark = pytest.mark.django_db

CUSTOM_HREF = re.compile(r'href="(/_theme/custom\.css\?s=[^"]+)"')


def _manifest(**changes) -> str:
    manifest = json.loads(example_files()["manifest.json"])
    manifest.update(changes)
    return json.dumps(manifest)


LOGOS = [
    {"id": "pixel", "label": "Piksel", "light": "assets/img/pixel.png", "dark": "assets/img/pixel.png"},
    {"id": "second", "label": "Drugi", "dark": "assets/img/pixel.png"},
]
FONTS = [
    {"id": "demo", "label": "Demo", "body": '"Demo Sans", system-ui, sans-serif'},
    {"id": "system", "label": "Systemowe", "body": "system-ui, sans-serif", "display": "Georgia, serif"},
]


@pytest.fixture
def example(competition):
    version, result = services.install_package(
        zip_with({"manifest.json": _manifest(logos=LOGOS, fonts=FONTS)})
    )
    assert result.errors == [], result.errors
    return version


@pytest.fixture
def themed(competition, example):
    services.activate(competition, example)
    competition.refresh_from_db()
    return competition


# --- manifest -------------------------------------------------------------------------------------


def test_manifest_logos_and_fonts_are_validated(example):
    assert [entry["id"] for entry in example.manifest["logos"]] == ["pixel", "second"]
    assert example.manifest["fonts"][1]["display"] == "Georgia, serif"


@pytest.mark.parametrize(
    ("logos", "fonts", "fragment"),
    [
        ([{"id": "x", "label": "X", "light": "assets/nope.svg"}], None, "assets/"),
        ([{"id": "x", "label": "X", "light": "theme.css"}], None, "assets/"),
        ([{"id": "Bad Id", "label": "X", "light": "assets/img/pixel.png"}], None, "id"),
        ([{"id": "x", "label": "X"}], None, "light"),
        (None, [{"id": "f", "label": "F", "body": "x; } body { color: red"}], "fonts.f.body"),
        (None, [{"id": "f", "label": "F", "body": "url(https://evil/x.woff2)"}], "fonts.f.body"),
        (None, [{"id": "f", "label": "F"}], "body"),
    ],
)
def test_bad_logos_and_fonts_reject_package(logos, fonts, fragment):
    changes = {}
    if logos is not None:
        changes["logos"] = logos
    if fonts is not None:
        changes["fonts"] = fonts
    version, result = services.install_package(zip_with({"manifest.json": _manifest(**changes)}))
    assert result.errors and any(fragment in error for error in result.errors), result.errors


# --- opcje i kontrast -----------------------------------------------------------------------------


def test_clean_options_keeps_only_declared_values(example):
    runtime = runtime_for(example.pk)
    cleaned = services.clean_options(
        runtime,
        {
            "scheme": "dark",
            "logo": "second",
            "font": "nope",
            "colors": {
                "light": {
                    "accent": "#AA0000",
                    "bg": "red; } body {",
                    "nonexistent": "#000000",
                    "text": "#14123a",
                },
                "dark": {"primary": "#123456"},
                "evil": {"bg": "#000000"},
            },
        },
    )
    assert cleaned["scheme"] == "dark" and cleaned["logo"] == "second" and "font" not in cleaned
    # Wielkie litery → małe; wartość równa domyślnej i spoza listy tokenów – pominięte.
    assert cleaned["colors"] == {"light": {"accent": "#aa0000"}, "dark": {"primary": "#123456"}}


def test_contrast_regression_blocks_save(themed, example):
    with pytest.raises(customize.CustomizationError) as excinfo:
        services.save_customization(themed, example, {"colors": {"light": {"text": "#fefefe"}}})
    assert any("text" in message and "4.5" in message for message in excinfo.value.messages)
    # Aktywacja zapisuje opcje wersji (bez kolorów) – odrzucony zapis/podgląd niczego nie dokłada.
    assert not any(row.options.get("colors") for row in ThemeCustomization.objects.all())
    assert not AuditLog.objects.filter(action=services.AUDIT_CUSTOMIZED).exists()


def test_focus_ring_needs_three_to_one(example):
    runtime = runtime_for(example.pk)
    errors, _ = customize.contrast_report(runtime, "auto", {"light": {"accent": "#fdfdfd"}})
    assert any("focus" in error for error in errors)


def test_good_colours_are_saved_audited_and_emitted(client_for, themed, example):
    cleaned = services.save_customization(
        themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}, "font": "system"}
    )
    assert cleaned["colors"] == {"light": {"accent": "#7a0d3c"}}
    themed.refresh_from_db()
    assert themed.theme_options["colors"] == {"light": {"accent": "#7a0d3c"}}
    entry = AuditLog.objects.get(action=services.AUDIT_CUSTOMIZED)
    assert entry.competition_id == themed.pk and entry.diff["after"]["font"] == "system"

    client = client_for(themed)
    html = client.get("/").content.decode()
    href = CUSTOM_HREF.search(html).group(1).replace("&amp;", "&")
    # Kolejność: tokens.css → theme.css → custom.css (nadpisanie wygrywa także z motywem).
    assert html.index("tokens.css") < html.index("theme.css") < html.index("/_theme/custom.css")
    response = client.get(href)
    assert response.status_code == 200 and response["Content-Type"].startswith("text/css")
    assert "immutable" in response["Cache-Control"]
    css = response.content.decode()
    assert "--t-accent: #7a0d3c;" in css
    # Pochodne liczone od nowa z nadpisanego akcentu (nie zostają z paczki).
    assert "--t-accent-600: #7a0d3c;" in css and "--t-on-accent: #ffffff;" in css
    assert "--t-font-display: Georgia, serif;" in css
    assert "<" not in css and "url(" not in css


def test_custom_css_url_is_stable_and_tamper_proof(client_for, themed, example, other_competition):
    services.save_customization(themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    themed.refresh_from_db()
    client = client_for(themed)
    first = CUSTOM_HREF.search(client.get("/").content.decode()).group(1)
    second = CUSTOM_HREF.search(client.get("/").content.decode()).group(1)
    assert first == second
    token = first.split("?s=", 1)[1]
    assert client.get("/_theme/custom.css?s=" + token[:-2] + "xx").status_code == 404
    assert client.get("/_theme/custom.css?s=").status_code == 404
    # Podpisany adres konkursu A nie działa pod domeną konkursu B.
    assert client_for(other_competition).get(first).status_code == 404


def test_scheme_switch_regenerates_full_palette(client_for, themed, example):
    services.save_customization(themed, example, {"scheme": "dark"})
    themed.refresh_from_db()
    client = client_for(themed)
    html = client.get("/").content.decode()
    assert 'data-color-scheme="dark"' in html
    css = client.get(CUSTOM_HREF.search(html).group(1)).content.decode()
    assert "color-scheme: dark;" in css and "--t-bg: #0b0a24;" in css
    assert "@media (prefers-color-scheme: dark)" not in css


def test_no_customization_no_link_and_old_csp(client_for, themed, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "https://s3.example.test"
    response = client_for(themed).get("/")
    html = response.content.decode()
    assert "/_theme/custom.css" not in html
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
    assert response["Content-Security-Policy"] == build_policy(nonce, theme_assets=True)


def test_customized_page_keeps_csp_and_survives_page_cache_hit(client_for, themed, example, settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "https://s3.example.test"
    settings.PAGE_CACHE_ENABLED = True
    services.save_customization(themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    themed.refresh_from_db()
    client = client_for(themed)
    miss = client.get("/")
    hit = client.get("/")
    assert miss["X-Page-Cache"] == "MISS" and hit["X-Page-Cache"] == "HIT"
    for response in (miss, hit):
        nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)
        # Arkusz dostosowania idzie z własnej domeny – polityka ta sama co dla motywu bez dostosowania.
        assert response["Content-Security-Policy"] == build_policy(nonce, theme_assets=True)
        assert "/_theme/custom.css?s=" in response.content.decode()
        assert "style=" not in response.content.decode().split("<body", 1)[0]


def test_save_invalidates_guest_page_cache(client_for, themed, example, settings):
    settings.PAGE_CACHE_ENABLED = True
    client = client_for(themed)
    client.get("/")
    assert client.get("/")["X-Page-Cache"] == "HIT"
    services.save_customization(themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    after = client.get("/")
    assert after["X-Page-Cache"] == "MISS" and "/_theme/custom.css" in after.content.decode()


def test_activation_restores_stored_customization(themed, example):
    services.save_customization(
        themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}, "logo": "second"}
    )
    services.activate(themed, None)
    themed.refresh_from_db()
    assert "colors" not in themed.theme_options
    services.activate(themed, example, {"layouts": {"header": "split"}})
    themed.refresh_from_db()
    assert themed.theme_options["colors"] == {"light": {"accent": "#7a0d3c"}}
    assert themed.theme_options["logo"] == "second"
    assert themed.theme_options["layouts"]["header"] == "split"


def test_reset_restores_package_defaults(client_for, themed, example):
    services.save_customization(themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    services.reset_customization(themed, example)
    themed.refresh_from_db()
    assert "colors" not in themed.theme_options
    assert "/_theme/custom.css" not in client_for(themed).get("/").content.decode()
    assert AuditLog.objects.filter(action=services.AUDIT_CUSTOMIZATION_RESET).exists()


def test_high_contrast_still_wins(client_for, themed, example):
    """Dostosowanie zmienia tokeny ``--t-*``; tryb wysokiego kontrastu nadpisuje role – i dalej wygrywa."""
    services.save_customization(themed, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    themed.refresh_from_db()
    client = client_for(themed)
    css = client.get(CUSTOM_HREF.search(client.get("/").content.decode()).group(1)).content.decode()
    assert "data-contrast" not in css and "--accent:" not in css and "--bg:" not in css


def test_logo_reaches_slot_context(example):
    from apps.themes.runtime import _build

    runtime = runtime_for(example.pk)
    theme = _build(runtime, {"logo": "second"}, None, preview=False)
    assert theme.context["logo"] == {
        "id": "second",
        "label": "Drugi",
        "light": "",
        "dark": runtime.assets_url + "assets/img/pixel.png",
    }
