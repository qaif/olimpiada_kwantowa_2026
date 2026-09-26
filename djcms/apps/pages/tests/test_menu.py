"""Menu serwisu ``dj.`` (§ 6.3 docs/tasks/DJ-01.md): drzewo django CMS, ``DjMenuModifier``, ``MenuExtension``.

Odpowiedniki reguł menu Wagtaila (``backend/apps/cms/context_processors.py``):
``primary`` ↔ ``PRIMARY_MENU_SLUGS``, ``expand`` ↔ ``DocumentIndexPage``, ``promote`` ↔
``PROMOTED_DOCUMENT_SLUGS``; kolejność = drzewo (domek pierwszy, wyniesione zaraz za nim),
„Dla szkół/nauczycieli” na końcu zależnie od ``chrome``.
"""

import re

import pytest

from apps.pages.cms_menus import DjMenuModifier
from apps.pages.models import MenuExtension


def _nav(html: str) -> str:
    return re.search(r'<nav class="nav nav--cms".*?</nav>', html, re.S).group(0)


def _primary(html: str) -> str | None:
    match = re.search(r'<nav class="nav nav--primary".*?</nav>', html, re.S)
    return match.group(0) if match else None


def _labels(nav: str) -> list[str]:
    """Kolejne pozycje menu głównego: tekst odnośnika/podsumowania, domek jako „[dom]”."""
    labels = []
    for match in re.finditer(
        r'<a class="nav__link nav__link--home"|<summary class="nav__link[^"]*">([^<]+)</summary>'
        r'|<a class="nav__link" [^>]*>([^<]+)</a>',
        nav,
    ):
        if match.group(0).startswith('<a class="nav__link nav__link--home"'):
            labels.append("[dom]")
        else:
            labels.append((match.group(1) or match.group(2)).strip())
    return labels


@pytest.fixture
def tree(make_page):
    home = make_page("Strona główna", "strona-glowna", home=True)
    zadania = make_page("Zadania", "zadania")
    make_page("Wyniki", "wyniki")
    dokumenty = make_page("Dokumenty", "dokumenty")
    make_page("Regulamin", "regulamin", parent=dokumenty)
    komitety = make_page("Skład komitetów", "komitety", parent=dokumenty, menu_title="Komitety")
    aktualnosci = make_page("Aktualności", "aktualnosci")
    make_page("Nowy etap", "nowy-etap", parent=aktualnosci)
    make_page("Ukryta", "ukryta", in_navigation=False)
    make_page("Szkic", "szkic", publish=False)
    MenuExtension.objects.create(extended_object=zadania, primary=True)
    MenuExtension.objects.create(extended_object=dokumenty, expand=True)
    MenuExtension.objects.create(extended_object=komitety, promote=True)
    return {"home": home, "dokumenty": dokumenty}


@pytest.mark.django_db
def test_menu_order_expand_promote_and_hidden(client, tree):
    nav = _nav(client.get("/wyniki/").content.decode())
    assert _labels(nav) == ["[dom]", "Komitety", "Zadania", "Wyniki", "Dokumenty", "Aktualności"]
    # Lista rozwijana tylko przy „expand”, bez wyniesionej pozycji.
    details = re.findall(r'<details class="nav-menu">.*?</details>', nav, re.S)
    assert len(details) == 1
    assert 'href="/dokumenty/regulamin/"' in details[0]
    assert "komitety" not in details[0]
    # Strona z dziećmi bez „expand” (aktualności) – zwykły odnośnik, bez dzieci w nagłówku.
    assert "nowy-etap" not in nav
    for absent in ("Ukryta", "Szkic"):
        assert absent not in nav
    assert 'href="/dokumenty/komitety/"' in nav


@pytest.mark.django_db
def test_primary_bar_lists_only_primary_pages(client, tree):
    html = client.get("/wyniki/").content.decode()
    primary = _primary(html)
    assert primary is not None
    assert re.findall(r'href="([^"]+)"', primary) == ["/zadania/"]


@pytest.mark.django_db
def test_primary_bar_absent_without_primary_pages(client, make_page):
    make_page("Strona główna", "strona-glowna", home=True)
    make_page("Wyniki", "wyniki")
    assert _primary(client.get("/wyniki/").content.decode()) is None


@pytest.mark.django_db
def test_active_states(client, tree):
    nav = _nav(client.get("/wyniki/").content.decode())
    assert re.search(r'href="/wyniki/"\s+aria-current="page">Wyniki', nav)
    assert "nav-menu__summary--active" not in nav

    nav = _nav(client.get("/dokumenty/regulamin/").content.decode())
    assert "nav-menu__summary nav-menu__summary--active" in nav
    assert re.search(r'href="/dokumenty/regulamin/"\s+aria-current="page"', nav)

    # Wyniesiona pozycja nie zapala już listy „Dokumenty” (tak jak w Wagtailu).
    html = client.get("/dokumenty/komitety/").content.decode()
    nav = _nav(html)
    assert re.search(r'href="/dokumenty/komitety/"\s+aria-current="page">Komitety', nav)
    assert "nav-menu__summary--active" not in nav

    nav = _nav(client.get("/").content.decode())
    assert re.search(r'nav__link--home" href="/" title="Strona główna"\s+aria-current="page"', nav)


@pytest.mark.django_db
def test_primary_item_active_on_its_page(client, tree):
    primary = _primary(client.get("/zadania/").content.decode())
    assert re.search(r'href="/zadania/"\s+aria-current="page"', primary)


@pytest.mark.django_db
def test_menu_extension_lookup_is_one_query_per_page(client, tree):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        client.get("/wyniki/")
    ext_queries = [q for q in ctx.captured_queries if "dj_pages_menuextension" in q["sql"]]
    assert len(ext_queries) == 1  # pasek i menu serwisu dzielą jedno zapytanie


# --- „Dla szkół/nauczycieli” z chrome ---------------------------------------------------------------


def _supervisor(nav: str) -> str | None:
    match = re.search(
        r'<details class="nav-menu">\s*<summary class="nav__link nav-menu__summary">'
        r"Dla szkół/nauczycieli</summary>.*?</details>",
        nav,
        re.S,
    )
    return match.group(0) if match else None


@pytest.mark.django_db
def test_supervisor_menu_with_registration_and_posters(client, tree, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    nav = _nav(client.get("/wyniki/").content.decode())
    item = _supervisor(nav)
    assert item is not None
    assert re.findall(r'href="([^"]+)">([^<]+)</a>', item) == [
        ("https://olimpiada.example/register/supervisor/", "Rejestracja nauczyciela"),
        ("https://olimpiada.example/plakaty/", "Plakaty do pobrania"),
    ]
    # Ostatnia pozycja menu – za drzewem, przed sliderem.
    assert nav.index("Dla szkół/nauczycieli") > nav.index("Aktualności")
    assert nav.index("Dla szkół/nauczycieli") < nav.index("data-sponsor-slider")


@pytest.mark.django_db
def test_supervisor_menu_posters_only(client, tree, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload(supervisor_registration={"enabled": False, "url": None}))
    item = _supervisor(_nav(client.get("/wyniki/").content.decode()))
    assert re.findall(r'href="([^"]+)"', item) == ["https://olimpiada.example/plakaty/"]


@pytest.mark.django_db
def test_supervisor_menu_absent_when_nothing_to_offer(client, tree, main_api, chrome_payload):
    payload = chrome_payload(supervisor_registration={"enabled": False, "url": None})
    payload["links"]["posters"] = None
    main_api.set("chrome", payload)
    assert "Dla szkół/nauczycieli" not in client.get("/wyniki/").content.decode()


# --- modyfikator bez żądania HTTP --------------------------------------------------------------------


def test_modifier_ignores_breadcrumb_and_non_page_nodes():
    from menus.base import NavigationNode

    node = NavigationNode("Zewnętrzny", "https://example.org/", 1)
    modifier = DjMenuModifier(renderer=None)
    assert modifier.modify(None, [node], None, None, post_cut=False, breadcrumb=True) == [node]
    assert modifier.modify(None, [node], None, None, post_cut=False, breadcrumb=False) == [node]
    assert node.attr == {"dj_primary": False, "dj_expand": False, "dj_promote": False}
    assert modifier.modify(None, [node], None, None, post_cut=True, breadcrumb=False) == [node]
    assert node.attr["dj_active"] is False


# --- edycja przez redaktora ----------------------------------------------------------------------


@pytest.mark.django_db
def test_toolbar_offers_menu_settings_in_edit_mode(client, tree, superuser):
    from cms.models import PageContent
    from cms.toolbar.utils import get_object_edit_url

    content = PageContent.admin_manager.get(page=tree["dokumenty"], language="pl")
    client.force_login(superuser)
    # Opublikowana wersja nie jest edytowalna – django CMS przekierowuje do podglądu z paskiem;
    # pozycja jest tam (nieaktywna do czasu utworzenia wersji roboczej).
    response = client.get(get_object_edit_url(content), follow=True)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Ustawienia menu (dj.)" in html
    assert "/djcms/admin/dj_pages/menuextension/" in html


@pytest.mark.django_db
def test_menu_extension_admin_add_and_change(client, make_page, superuser):
    page = make_page("Kontakt", "kontakt")
    client.force_login(superuser)
    url = f"/djcms/admin/dj_pages/menuextension/add/?extended_object={page.pk}"
    assert client.get(url).status_code == 200
    response = client.post(url, {"primary": "on"})
    assert response.status_code in (200, 302)
    extension = MenuExtension.objects.get(extended_object=page)
    assert (extension.primary, extension.expand, extension.promote) == (True, False, False)
