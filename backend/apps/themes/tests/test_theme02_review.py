"""Poprawki THEME-02 po przeglądzie krytyka (H1, M1–M3, L1–L6) – każda z przypadkiem, który ją wywołał."""

from __future__ import annotations

import json
import re

import pytest
from django.core.management import call_command
from django.urls import set_script_prefix
from wagtail.models import Page

from apps.accounts.tests.factories import CoordinatorFactory
from apps.cms.models import ContentPage
from apps.core.models import AuditLog
from apps.tenancy.models import Competition
from apps.themes import customize, services
from apps.themes import menu as menu_mod
from apps.themes.models import SiteMenu, ThemeCustomization
from apps.themes.runtime import runtime_for

from .helpers import FIXTURES, example_files, zip_with

pytestmark = pytest.mark.django_db

MENU_URL = "/coordinator/competition/theme/menu/"
IQO_110 = FIXTURES / "iqo-quantum-1.1.0.zip"


def _flag(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "themes": True}
    competition.save(update_fields=["feature_flags"])


def _menu(client_for, competition):
    return client_for(competition).get("/").context["cms_menu"]


def _save(client_for, competition, items):
    default = _menu(client_for, competition)
    auto = {
        item["key"]: {
            "home": item.get("home", False),
            "has_children": bool(item["children"]),
            "title": item["title"],
        }
        for item in default
    }
    return services.save_menu(competition, items, auto_keys=auto, default_keys=[i["key"] for i in default])


def _nav(html: str) -> str:
    return re.search(r'<nav class="nav nav--cms".*?</nav>', html, re.S).group(0)


@pytest.fixture
def example(competition):
    version, result = services.install_package(zip_with())
    assert result.errors == []
    return version


# --- H1: rewizja menu nie powtarza się ------------------------------------------------------------


def test_save_reset_save_never_serves_stale_menu_from_another_process(client_for, competition):
    _save(
        client_for,
        competition,
        [{"key": "link-00000001", "type": "link", "url": "/a/", "labels": {"pl": "Stare"}}],
    )
    competition.refresh_from_db()
    assert "Stare" in _nav(client_for(competition).get("/").content.decode())
    # „Inny proces” pamięta menu sprzed resetu pod jego rewizją.
    other_process = dict(menu_mod._CACHE)
    services.reset_menu(competition)
    _save(
        client_for,
        competition,
        [{"key": "link-00000002", "type": "link", "url": "/b/", "labels": {"pl": "Nowe"}}],
    )
    competition.refresh_from_db()
    menu_mod._CACHE.update(other_process)
    nav = _nav(client_for(competition).get("/").content.decode())
    assert "Nowe" in nav and "Stare" not in nav
    assert competition.theme_options["menu"] == SiteMenu.objects.get(competition=competition).revision


# --- M1: nieaktualny formularz nie zmienia wierszy, których nie zawierał ----------------------------


def test_stale_form_keeps_rows_it_did_not_show(client_for, competition):
    _flag(competition)
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    _save(client_for, competition, [{"key": "home", "type": "auto", "labels": {"pl": "Start"}}])
    rows = client.get(MENU_URL).context["rows"]
    form = {"action": "save", "row_keys": [r["key"] for r in rows if r["key"] != "home"]}
    for position, row in enumerate(rows, start=1):
        form[f"order_{row['key']}"] = position
        form[f"visible_{row['key']}"] = "on"
    # W międzyczasie redakcja dodała stronę – formularz jej nie zna.
    new_page = competition.site.root_page.add_child(
        instance=ContentPage(title="Nowa zakładka", slug="nowa-zakladka-t2", show_in_menu=True)
    )
    assert client.post(MENU_URL, form).status_code == 302
    competition.refresh_from_db()
    menu = _menu(client_for, competition)
    keys = [item["key"] for item in menu]
    assert f"p{new_page.pk}" in keys  # nie została ukryta
    assert next(item for item in menu if item["key"] == "home")["title"] == "Start"  # etykieta przetrwała


# --- M2: pary kontrastu z tokens.json i z nazw ----------------------------------------------------


@pytest.fixture
def iqo(competition, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v0.43.0")
    version, result = services.install_package(IQO_110.read_bytes())
    assert result.errors == [], result.errors
    services.activate(competition, version)
    competition.refresh_from_db()
    return version


@pytest.mark.parametrize(
    ("colors", "token"),
    [
        ({"dark": {"primary-fill": "#ffff66"}}, "primary-fill"),
        ({"dark": {"accent-on-primary": "#15133b"}}, "accent-on-primary"),
        ({"light": {"primary-fill": "#ffff66"}}, "primary-fill"),
    ],
)
def test_critic_probes_are_blocked(competition, iqo, colors, token):
    options = {"colors": colors, **({"scheme": "auto"} if "light" in colors else {})}
    with pytest.raises(customize.CustomizationError) as excinfo:
        services.save_customization(competition, iqo, options)
    assert any(token in message for message in excinfo.value.messages)


def test_iqo_declares_pairs_and_brand_ink_comes_from_token():
    result = services.validate_package(IQO_110.read_bytes(), app_version="v0.43.0")
    assert ["on-primary-fill", "primary-fill", 4.5] in result.tokens.contrast
    assert "--brand-ink: var(--t-on-primary-fill" in result.theme_css


def test_declared_pair_with_literal_colour(competition):
    tokens = json.loads(example_files()["tokens.json"])
    tokens["colors"]["primary-fill"] = "#3c4bff"
    tokens["contrast"] = [["#ffffff", "primary-fill"]]
    version, result = services.install_package(zip_with({"tokens.json": json.dumps(tokens)}))
    assert result.errors == []
    runtime = runtime_for(version.pk)
    errors, _ = customize.contrast_report(runtime, "auto", {"light": {"primary-fill": "#eeeeee"}})
    assert any("#ffffff" in error and "primary-fill" in error for error in errors)


@pytest.mark.parametrize(
    "pairs", ['"x"', '[["a"]]', '[["text", "bg", 99]]', '[["url(x)", "bg"]]', '[["text", "bg", true]]']
)
def test_bad_contrast_declarations_reject_package(pairs):
    tokens = json.loads(example_files()["tokens.json"])
    tokens["contrast"] = json.loads(pairs)
    _version, result = services.install_package(zip_with({"tokens.json": json.dumps(tokens)}))
    assert any("contrast" in error or "kontrast" in error for error in result.errors), result.errors


def test_name_rules_on_y_suffix_and_a_on_x():
    pairs = {
        (fg, bg)
        for fg, bg, *_ in customize._pairs({"cover": "#000", "on-cover-soft": "#fff", "x-on-cover": "#fff"})
    }
    assert ("on-cover-soft", "cover") in pairs and ("x-on-cover", "cover") in pairs


# --- M3: aktywacja odrzuca nieczytelne kolory; theme_install przenosi tylko układy i opcje --------


def test_activation_drops_colours_failing_contrast(competition, example):
    ThemeCustomization.objects.create(
        competition=competition, theme_version=example, options={"colors": {"light": {"text": "#fefefe"}}}
    )
    services.activate(competition, example)
    competition.refresh_from_db()
    assert "colors" not in competition.theme_options
    assert any("text" in warning for warning in competition.theme_activation_warnings)
    entry = AuditLog.objects.filter(action=services.AUDIT_ACTIVATED).latest("pk")
    assert entry.diff["dropped_colors"] == {"light": {"text": "#fefefe"}}


def test_gallery_activation_warns_about_dropped_colours(client_for, competition, example):
    _flag(competition)
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    ThemeCustomization.objects.create(
        competition=competition, theme_version=example, options={"colors": {"light": {"text": "#fefefe"}}}
    )
    response = client.post(
        "/coordinator/competition/theme/", {"version": example.pk, "action": "activate"}, follow=True
    )
    assert "Kolory dostosowania pominięte" in response.content.decode()


def test_theme_install_carries_layouts_not_colours(competition, example, tmp_path):
    services.activate(competition, example, {"layouts": {"header": "split"}, "brand_accent": True})
    services.save_customization(
        competition,
        example,
        {
            "layouts": {"header": "split"},
            "brand_accent": True,
            "colors": {"light": {"accent": "#7a0d3c"}},
            "scheme": "dark",
        },
    )
    competition.refresh_from_db()
    manifest = json.loads(example_files()["manifest.json"])
    manifest["version"] = "1.2.0"
    package = tmp_path / "next.zip"
    package.write_bytes(zip_with({"manifest.json": json.dumps(manifest)}))
    call_command("theme_install", str(package), "--activate", competition.slug)
    competition.refresh_from_db()
    options = competition.theme_options
    assert options["layouts"]["header"] == "split" and options["brand_accent"] is True
    assert options["scheme"] == "dark"
    assert "colors" not in options


# --- L1: zapisy scalają świeże theme_options ------------------------------------------------------


def test_menu_save_on_stale_object_keeps_concurrent_colours(client_for, competition, example):
    services.activate(competition, example)
    stale = Competition.objects.get(pk=competition.pk)
    services.save_customization(competition, example, {"colors": {"light": {"accent": "#7a0d3c"}}})
    _save(client_for, stale, [{"key": "home", "type": "auto", "hidden": True}])
    fresh = Competition.objects.get(pk=competition.pk)
    assert fresh.theme_options["colors"] == {"light": {"accent": "#7a0d3c"}}
    assert fresh.theme_options["menu"]


# --- L2: wersja generatora w podpisie -------------------------------------------------------------


def test_signed_payload_carries_generator_version(example):
    runtime = runtime_for(example.pk)
    payload = customize.unsign(customize.sign(1, runtime, {"colors": {"light": {"accent": "#7a0d3c"}}}))
    assert payload["g"] == customize.GENERATOR_VERSION


# --- L3: ukryta grupa ukrywa członków --------------------------------------------------------------


def test_hidden_group_hides_its_members(client_for, competition):
    _save(
        client_for,
        competition,
        [
            {"key": "group-00000001", "type": "group", "hidden": True, "labels": {"pl": "Schowane"}},
            {
                "key": "link-00000001",
                "type": "link",
                "url": "/x/",
                "labels": {"pl": "Członek"},
                "parent": "group-00000001",
            },
        ],
    )
    competition.refresh_from_db()
    nav = _nav(client_for(competition).get("/").content.decode())
    assert "Schowane" not in nav and "Członek" not in nav


# --- L4: zapisana strona niedostępna --------------------------------------------------------------


def test_unpublished_linked_page_does_not_block_and_is_shown(client_for, competition):
    _flag(competition)
    page = competition.site.root_page.add_child(
        instance=ContentPage(title="Do wycofania", slug="do-wycofania-t2")
    )
    _save(
        client_for,
        competition,
        [{"key": "link-00000001", "type": "link", "page": page.pk, "labels": {"pl": "Skrót"}}],
    )
    Page.objects.filter(pk=page.pk).update(live=False)
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    editor = client.get(MENU_URL)
    assert "(niedostępna strona)" in editor.content.decode()
    rows = editor.context["rows"]
    form = {"action": "save", "row_keys": [r["key"] for r in rows]}
    for position, row in enumerate(rows, start=1):
        form[f"order_{row['key']}"] = position
        form[f"visible_{row['key']}"] = "on"
        if row["type"] == "link":
            form[f"page_{row['key']}"] = str(page.pk)
            form[f"label_{row['key']}_pl"] = "Skrót"
    assert client.post(MENU_URL, form).status_code == 302
    competition.refresh_from_db()
    assert "Skrót" not in _nav(client_for(competition).get("/").content.decode())


def test_error_names_the_broken_row(client_for, competition, other_competition):
    tree_root = Page.objects.filter(depth=1).first()
    foreign = tree_root.add_child(instance=Page(title="Obca", slug="obca-t2"))
    with pytest.raises(menu_mod.MenuError, match="Mój odnośnik"):
        _save(
            client_for,
            competition,
            [{"key": "link-00000001", "type": "link", "page": foreign.pk, "labels": {"pl": "Mój odnośnik"}}],
        )
    with pytest.raises(menu_mod.MenuError, match="Zły adres"):
        _save(
            client_for,
            competition,
            [{"key": "link-00000001", "type": "link", "url": "javascript:x", "labels": {"pl": "Zły adres"}}],
        )


# --- L5: kolejność arkuszy i ostrzeżenie o --t-* w theme.css --------------------------------------


def test_theme_css_setting_platform_tokens_is_a_warning():
    css = example_files()["theme.css"].decode() + "\n:root { --t-accent: #000; }\n"
    version, result = services.install_package(zip_with({"theme.css": css}))
    assert result.errors == [] and version.is_valid
    assert any("--t-accent" in warning for warning in result.warnings)


# --- L6: arkusz akcentu marki z palety efektywnej -------------------------------------------------


def test_brand_accent_uses_effective_palette(client_for, competition, example):
    competition.accent_colour = "#0055aa"
    competition.save(update_fields=["accent_colour"])
    services.activate(competition, example, {"brand_accent": True})
    competition.refresh_from_db()
    client = client_for(competition)
    before = re.search(r'href="(/_theme/overrides\.css\?v=[^"]+)"', client.get("/").content.decode()).group(1)
    services.save_customization(
        competition, example, {"brand_accent": True, "colors": {"light": {"bg": "#f0f0e0"}}}
    )
    competition.refresh_from_db()
    after = re.search(r'href="(/_theme/overrides\.css\?v=[^"]+)"', client.get("/").content.decode()).group(1)
    assert after != before and after.startswith(before)
    css = client.get(after).content.decode()
    from apps.themes.tokens import mix

    assert f"--t-accent-soft: {mix('#f0f0e0', '#0055aa', 0.12)};" in css


# --- L7: odnośnik wewnętrzny w trybie prefiksu ścieżki --------------------------------------------


def test_internal_link_gets_path_prefix(rf, competition):
    items = [{"key": "link-00000001", "type": "link", "url": "/kontakt/", "labels": {"pl": "Kontakt"}}]
    set_script_prefix("/druga/")
    try:
        request = rf.get("/druga/kontakt/")
        menu = menu_mod.build_menu([], items, request, competition, language="pl")
    finally:
        set_script_prefix("/")
    assert menu[0]["url"] == "/druga/kontakt/" and menu[0]["active"] is True and menu[0]["external"] is False
