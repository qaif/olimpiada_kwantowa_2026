"""Nadpisania menu serwisu (THEME-02 § 1): walidacja, render, izolacja konkursów, koszt zapytań."""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from wagtail.models import Page

from apps.core.models import AuditLog
from apps.themes import menu as menu_mod
from apps.themes import services
from apps.themes.models import SiteMenu

pytestmark = pytest.mark.django_db


# --- walidacja adresów i etykiet ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://iqo-official.org/rules/",
        "http://example.org",
        "/dokumenty/regulamin/",
        "/",
        "https://a.b/x?y=1#z",
    ],
)
def test_clean_url_accepts_http_https_and_internal_paths(url):
    assert menu_mod.clean_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox",
        "mailto:a@b.c",
        "//evil.example/",
        "/\\evil.example",
        "regulamin/",
        "https://user:pass@evil.example/",
        "ftp://example.org/",
        "https://",
        "",
        "/a b",
        "/x" + "y" * 600,
    ],
)
def test_clean_url_rejects_everything_else(url):
    with pytest.raises(menu_mod.MenuError):
        menu_mod.clean_url(url)


def test_clean_label_limits_and_control_characters():
    assert menu_mod.clean_label("  Wyniki   finału ") == "Wyniki finału"
    with pytest.raises(menu_mod.MenuError):
        menu_mod.clean_label("a" * 61)
    with pytest.raises(menu_mod.MenuError):
        menu_mod.clean_label("zła\x07linia")
    # Separatory wierszy Unicode to białe znaki – zamieniane na spację, nie przechodzą do etykiety.
    assert menu_mod.clean_label("zła linia") == "zła linia"


# --- render ---------------------------------------------------------------------------------------


def _default_menu(client_for, competition) -> list[dict]:
    return client_for(competition).get("/").context["cms_menu"]


def _keys(menu):
    return [item["key"] for item in menu]


def _nav(html: str) -> str:
    return re.search(r'<nav class="nav nav--cms".*?</nav>', html, re.S).group(0)


def _save(competition, items, client_for):
    default = _default_menu(client_for, competition)
    auto = {
        item["key"]: {"home": item.get("home", False), "has_children": bool(item["children"])}
        for item in default
    }
    return services.save_menu(competition, items, auto_keys=auto, default_keys=_keys(default))


def test_default_menu_has_stable_keys(client_for, competition):
    menu = _default_menu(client_for, competition)
    assert menu[0]["key"] == "home"
    assert all(re.match(r"^(home|teachers|p\d+|f:.+)$", key) for key in _keys(menu))


def test_no_overrides_is_the_same_list_without_queries(rf, competition):
    menu = [{"key": "home", "title": "x", "url": "/", "children": []}]
    request = rf.get("/")
    request.competition = competition
    with CaptureQueriesContext(connection) as queries:
        assert menu_mod.apply_overrides(menu, request) is menu
    assert queries.captured_queries == []


def test_reorder_hide_rename_link_and_group(client_for, competition, english_enabled_site):
    english_enabled_site(competition)
    competition.refresh_from_db()
    default = _default_menu(client_for, competition)
    keys = _keys(default)
    zadania = next(item["key"] for item in default if item["slug"] == "zadania")
    wyniki = next(item["key"] for item in default if item["slug"] == "wyniki")
    items = [
        {"key": "home", "type": "auto"},
        {"key": wyniki, "type": "auto", "labels": {"pl": "Rezultaty", "en": "Final results"}},
        {"key": zadania, "type": "auto", "hidden": True},
        {"key": "group-0000aaaa", "type": "group", "labels": {"pl": "Więcej", "en": "More"}},
        {
            "key": "link-0000bbbb",
            "type": "link",
            "url": "https://iqo-official.org/rules/",
            "new_tab": True,
            "labels": {"en": "Rules <script>alert(1)</script>"},
            "parent": "group-0000aaaa",
        },
        {"key": "link-0000cccc", "type": "link", "url": "/kontakt/", "labels": {"pl": "Napisz do nas"}},
    ]
    _save(competition, items, client_for)
    competition.refresh_from_db()
    assert competition.theme_options["menu"] == 1
    entry = AuditLog.objects.get(action=services.AUDIT_MENU_SAVED)
    assert entry.competition_id == competition.pk and entry.diff["revision"] == 1

    client = client_for(competition)
    html = client.get("/").content.decode()
    nav = _nav(html)
    assert "Rezultaty" in nav and "Zadania" not in nav
    assert nav.index("Rezultaty") < nav.index("Więcej") < nav.index("Napisz do nas")
    # Odnośnik w grupie: escapowana etykieta, nowa karta z ``noopener``.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in nav and "<script>alert" not in html
    assert 'href="https://iqo-official.org/rules/" target="_blank" rel="noopener noreferrer"' in nav
    assert '<details class="nav-menu">' in nav
    # Pozycje, których koordynator nie wymienił, stoją na końcu (nic nie znika po cichu).
    rest = [key for key in keys if key not in (zadania, wyniki, "home")]
    menu_keys = _keys(client.get("/").context["cms_menu"])
    assert menu_keys[-len(rest) :] == rest if rest else True

    client.cookies["django_language"] = "en"
    english = _nav(client.get("/", HTTP_ACCEPT_LANGUAGE="en").content.decode())
    assert "Final results" in english and "More" in english
    # Własny odnośnik bez etykiety angielskiej schodzi na język domyślny konkursu.
    assert "Napisz do nas" in english


def test_hidden_item_leaves_sticky_bar_too(client_for, competition):
    default = _default_menu(client_for, competition)
    zadania = next(item["key"] for item in default if item["slug"] == "zadania")
    _save(competition, [{"key": zadania, "type": "auto", "hidden": True}], client_for)
    competition.refresh_from_db()
    context = client_for(competition).get("/").context
    assert zadania not in _keys(context["cms_menu"])
    assert zadania not in _keys(context["cms_menu_primary"])


def test_default_equivalent_save_removes_overrides(client_for, competition):
    default = _default_menu(client_for, competition)
    _save(competition, [{"key": "home", "type": "auto", "hidden": True}], client_for)
    competition.refresh_from_db()
    assert SiteMenu.objects.filter(competition=competition).exists()
    _save(competition, [{"key": key, "type": "auto"} for key in _keys(default)], client_for)
    competition.refresh_from_db()
    assert not SiteMenu.objects.filter(competition=competition).exists()
    assert "menu" not in competition.theme_options
    assert AuditLog.objects.filter(action=services.AUDIT_MENU_RESET).exists()


def test_menu_marker_survives_theme_activation(client_for, competition):
    _save(competition, [{"key": "home", "type": "auto", "hidden": True}], client_for)
    competition.refresh_from_db()
    services.activate(competition, None)
    competition.refresh_from_db()
    assert competition.theme_options == {"menu": 1}


def test_revision_change_reaches_other_processes(client_for, competition):
    """Pamięć procesu trzyma rewizję – nowa rewizja (zapis w innym procesie) jest czytana od nowa."""
    _save(competition, [{"key": "home", "type": "auto", "labels": {"pl": "Start"}}], client_for)
    competition.refresh_from_db()
    assert "Start" in _nav(client_for(competition).get("/").content.decode())
    row = SiteMenu.objects.get(competition=competition)
    row.items = [{**row.items[0], "labels": {"pl": "Początek"}}]
    row.revision = 2
    row.save()
    competition.theme_options = {**competition.theme_options, "menu": 2}
    competition.save(update_fields=["theme_options"])
    assert "Początek" in _nav(client_for(competition).get("/").content.decode())


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (
            [
                {"key": "home", "type": "auto", "parent": "group-00000001"},
                {"key": "group-00000001", "type": "group", "labels": {"pl": "G"}},
            ],
            "głównej",
        ),
        (
            [
                {"key": "group-00000001", "type": "group", "labels": {"pl": "G"}, "parent": "group-00000002"},
                {"key": "group-00000002", "type": "group", "labels": {"pl": "H"}},
            ],
            "innej grupy",
        ),
        (
            [{"key": "link-00000001", "type": "link", "url": "javascript:alert(1)", "labels": {"pl": "x"}}],
            "https",
        ),
        ([{"key": "link-00000001", "type": "link", "url": "/x/", "labels": {}}], "etykietę"),
        ([{"key": "link-00000001", "type": "link", "url": "/x/", "labels": {"de": "x"}}], "de"),
        ([{"key": 'evil"><script>', "type": "auto"}], "Nieznana"),
        ([{"key": "link-00000001", "type": "group", "labels": {"pl": "x"}}], "kluczem"),
    ],
)
def test_invalid_items_are_rejected(client_for, competition, items, message):
    with pytest.raises(menu_mod.MenuError, match=message):
        _save(competition, items, client_for)
    assert not SiteMenu.objects.exists()


def test_page_of_another_competition_cannot_be_linked(client_for, competition, other_competition):
    tree_root = Page.objects.filter(depth=1).first()
    foreign = tree_root.add_child(instance=Page(title="Obca strona", slug="obca-strona-theme02"))
    other_competition.site.root_page = foreign
    other_competition.site.save()
    with pytest.raises(menu_mod.MenuError, match="nie należy"):
        _save(
            competition,
            [{"key": "link-00000001", "type": "link", "page": foreign.pk, "labels": {"pl": "Obca"}}],
            client_for,
        )


def test_internal_page_link_resolves_url_in_own_tree(client_for, competition):
    page = competition.site.root_page.get_children().live().first()
    _save(
        competition,
        [{"key": "link-00000001", "type": "link", "page": page.pk, "labels": {"pl": "Skrót do strony"}}],
        client_for,
    )
    competition.refresh_from_db()
    nav = _nav(client_for(competition).get("/").content.decode())
    assert f'href="{page.url}"' in nav and "Skrót do strony" in nav


def test_overrides_do_not_leak_to_another_competition(client_for, competition, other_competition):
    _save(
        competition,
        [{"key": "link-00000001", "type": "link", "url": "/x/", "labels": {"pl": "Tylko nasz"}}],
        client_for,
    )
    assert "Tylko nasz" not in client_for(other_competition).get("/").content.decode()


def test_reset_menu_restores_default(client_for, competition):
    default = _default_menu(client_for, competition)
    _save(competition, [{"key": "home", "type": "auto", "hidden": True}], client_for)
    competition.refresh_from_db()
    services.reset_menu(competition)
    competition.refresh_from_db()
    assert "menu" not in (competition.theme_options or {})
    assert _keys(_default_menu(client_for, competition)) == _keys(default)
