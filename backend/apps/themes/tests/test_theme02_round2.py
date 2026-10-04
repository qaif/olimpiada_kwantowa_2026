"""THEME-02 – druga, niezależna runda przeglądu: cyfry Unicode, znaki formatujące, palety, pary, aktywacja."""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.themes import customize, services
from apps.themes import menu as menu_mod
from apps.themes.models import SiteMenu, ThemeCustomization
from apps.themes.tokens import contrast

from .helpers import FIXTURES, zip_with

pytestmark = pytest.mark.django_db

MENU_URL = "/coordinator/competition/theme/menu/"
CUSTOM_URL = "/coordinator/competition/theme/customize/"
GALLERY_URL = "/coordinator/competition/theme/"
IQO_110 = FIXTURES / "iqo-quantum-1.1.0.zip"
UNICODE_DIGITS = ["١٢", "²", "１２", "12²"]


@pytest.fixture
def coordinator(client_for, competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "themes": True}
    competition.save(update_fields=["feature_flags"])
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    return client


@pytest.fixture
def example(competition):
    version, result = services.install_package(zip_with())
    assert result.errors == []
    return version


# --- 1. cyfry spoza ASCII nie dają 500 ------------------------------------------------------------


@pytest.mark.parametrize("value", UNICODE_DIGITS)
def test_ascii_int_rejects_unicode_digits(value):
    assert menu_mod.ascii_int(value) is None
    assert menu_mod.ascii_int("12") == 12


@pytest.mark.parametrize("value", UNICODE_DIGITS)
def test_unicode_digits_in_menu_post_are_400_not_500(coordinator, value):
    response = coordinator.post(MENU_URL, {"action": "add_link", "new_page": value, "new_label_pl": "x"})
    assert response.status_code == 400
    assert not SiteMenu.objects.exists()
    rows = coordinator.get(MENU_URL).context["rows"]
    form = {"action": "save", "row_keys": [r["key"] for r in rows]}
    for row in rows:
        form[f"order_{row['key']}"] = value
        form[f"visible_{row['key']}"] = "on"
    assert coordinator.post(MENU_URL, form).status_code in (200, 302)


@pytest.mark.parametrize("value", UNICODE_DIGITS)
def test_unicode_digits_in_version_ids_are_404(coordinator, competition, value):
    assert coordinator.get(f"{CUSTOM_URL}?version={value}").status_code == 404
    assert coordinator.post(GALLERY_URL, {"version": value, "action": "activate"}).status_code == 404
    assert coordinator.get(f"/_theme/overrides.css?v={value}-0055aa").status_code == 404


# --- 2. znaki formatujące w etykietach ------------------------------------------------------------


@pytest.mark.parametrize("label", ["‮abc", "a​b", "⁦x⁩", "x﻿", "a­b", "؜x"])
def test_format_characters_are_rejected(label):
    with pytest.raises(menu_mod.MenuError):
        menu_mod.clean_label(label)


def test_zwj_zwnj_and_whitespace_normalisation():
    assert menu_mod.clean_label("می‌خواهم") == "می‌خواهم"
    assert menu_mod.clean_label("👩‍🔬") == "👩‍🔬"
    assert menu_mod.clean_label("  a  b \t c ") == "a b c"


# --- 3. zapis jednego schematu nie kasuje drugiej palety ------------------------------------------


def test_saving_dark_palette_keeps_light_overrides(coordinator, competition, example):
    services.activate(competition, example)
    services.save_customization(competition, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    response = coordinator.post(
        CUSTOM_URL,
        {"version": example.pk, "action": "save", "scheme": "dark", "color_dark_accent": "#ff8fb0"},
    )
    assert response.status_code == 302
    competition.refresh_from_db()
    assert competition.theme_options["colors"] == {
        "light": {"accent": "#7a0d3c"},
        "dark": {"accent": "#ff8fb0"},
    }


def test_palette_present_in_form_is_replaced_whole(coordinator, competition, example):
    """Pole przywrócone do wartości domyślnej usuwa nadpisanie tej palety (także bez innych zmian)."""
    services.activate(competition, example)
    services.save_customization(competition, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    runtime_default = customize.default_hex("#c2185b")
    coordinator.post(
        CUSTOM_URL, {"version": example.pk, "action": "save", "color_light_accent": runtime_default}
    )
    competition.refresh_from_db()
    assert "colors" not in competition.theme_options


# --- 4. pary ogólne: tekst każdego stopnia na każdej powierzchni ---------------------------------


@pytest.fixture
def iqo(competition, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v0.44.0")
    version, result = services.install_package(IQO_110.read_bytes())
    assert result.errors == [], result.errors
    services.activate(competition, version)
    competition.refresh_from_db()
    return version


@pytest.mark.parametrize(
    ("mode", "token", "value", "background", "low", "high"),
    [
        ("dark", "muted", "#26244f", "#0B0A24", 1.2, 1.45),
        ("dark", "text-soft", "#38366e", "#14123A", 1.4, 1.8),
        ("dark", "link", "#2f2d70", "#0B0A24", 1.2, 1.7),
        ("light", "cta", "#b4b9f2", "#F7F7FC", 1.4, 1.8),
        ("light", "muted", "#d0d1e8", "#FFFFFF", 1.2, 1.6),
    ],
)
def test_low_contrast_probes_are_blocked(competition, iqo, mode, token, value, background, low, high):
    assert low <= contrast(value, background) <= high  # sonda naprawdę ~1.3–1.6:1
    options = {"colors": {mode: {token: value}}, **({"scheme": "light"} if mode == "light" else {})}
    with pytest.raises(customize.CustomizationError) as excinfo:
        services.save_customization(competition, iqo, options)
    assert any(token in message for message in excinfo.value.messages)


def test_generic_pairs_cover_surfaces():
    palette = {
        name: "#000000" for name in ("text-soft", "muted", "link", "cta", "bg", "surface", "surface-2")
    }
    pairs = {(fg, bg) for fg, bg, *_ in customize._pairs(palette)}
    for fg in ("text-soft", "muted", "link", "cta"):
        for bg in ("bg", "surface", "surface-2"):
            assert (fg, bg) in pairs


# --- 5. aktywacja zapisuje przeniesione opcje przy nowej wersji -----------------------------------


def test_activation_persists_options_for_next_gallery_save(coordinator, competition, example):
    services.activate(competition, example, {"scheme": "dark", "brand_accent": False})
    row = ThemeCustomization.objects.get(competition=competition, theme_version=example)
    assert row.options["scheme"] == "dark"
    # „Zapisz opcje” w galerii wysyła tylko układy i akcent – schemat ma przetrwać.
    coordinator.post(GALLERY_URL, {"version": example.pk, "action": "activate", "layout_header": "split"})
    competition.refresh_from_db()
    assert competition.theme_options["scheme"] == "dark"
    assert competition.theme_options["layouts"]["header"] == "split"


# --- 6. min_app_version ---------------------------------------------------------------------------


def test_iqo_requires_app_with_theme02():
    from apps.themes.package import validate_package

    data = IQO_110.read_bytes()
    assert validate_package(data, app_version="v0.44.0").errors == []
    assert any("0.44.0" in error for error in validate_package(data, app_version="v0.43.0").errors)
