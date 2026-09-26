"""Strony-dane na żywo z Wagtaila (DJ-02 D9): ``WorkshopSchedulePlugin`` i ``PartnersLivePlugin``.

Tekst formatowany z API (``workshops.page.intro``, ``partners.page.intro``, ``become_partner_body``)
przychodzi **niesanityzowany** – testy sprawdzają, że ``<script>``, atrybuty ``on*`` i adresy
``javascript:`` nie docierają do strony, a zwykłe formatowanie tak.
"""

from __future__ import annotations

import urllib.error

import pytest

from apps.live import data
from apps.live.chrome import safe_href

pytestmark = pytest.mark.django_db

UNAVAILABLE = "chwilowo niedostępne w tej wersji serwisu"

HOSTILE_HTML = (
    '<p>Wstęp <strong>ważny</strong><script>alert("x")</script>'
    '<img src="x.png" onerror="alert(1)">'
    '<a href="javascript:alert(2)">zły</a> <a href="/zadania/" onclick="alert(3)">zadania</a></p>'
)


def _html(response) -> str:
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def _assert_clean(html: str) -> None:
    """Treść strony (``<main>``) bez skryptów, atrybutów zdarzeń i adresów ``javascript:``."""
    main = html.split("<main", 1)[1].split("</main>", 1)[0]
    assert "<script" not in main
    assert "alert(" not in main
    assert "onerror" not in main and "onclick" not in main and "onmouseover" not in main
    assert "javascript:" not in main


def _schedule(caption: str, rows: list[dict]) -> dict:
    return {
        "caption": caption,
        "topic_label": "",
        "date_label": "Kiedy",
        "time_label": "",
        "lecturer_label": "",
        "has_time": True,
        "has_lecturer": True,
        "rows": rows,
    }


def workshops_payload(**overrides) -> dict:
    payload = {
        "page_path": "/warsztaty/",
        "upcoming": [],
        "rows": [],
        "materials": {"show": False, "count": 0, "login_url": "", "materials_url": ""},
        "page": {"title": "Warsztaty", "intro": HOSTILE_HTML},
        "schedules": [
            _schedule(
                "Jesień",
                [
                    {"topic": "Kubity <b>1</b>", "date": "1 października", "time": "10:00", "lecturer": ""},
                    {"topic": "Splątanie", "date": "do potwierdzenia", "time": "", "lecturer": ""},
                ],
            ),
            _schedule("Wiosna", [{"topic": "Pomiar", "date": "1 marca", "time": "", "lecturer": "dr Y"}]),
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def workshops_page(live_page):
    return live_page(
        "Warsztaty",
        "warsztaty",
        "dj/pages/content.html",
        intro=[("WorkshopSchedulePlugin", {"part": "intro"})],
        body=[
            ("TextPlugin", {"body": "<p>Stała treść</p>"}),
            ("WorkshopSchedulePlugin", {"part": "schedule"}),
        ],
    )


def test_workshops_intro_and_tables_come_live_from_api(client, api_up, workshops_page):
    api_up.set("workshops", workshops_payload())
    html = _html(client.get("/warsztaty/"))
    # Wprowadzenie po sanityzatorze – formatowanie zostaje, skrypty i zdarzenia nie.
    assert "Wstęp <strong>ważny</strong>" in html
    assert 'href="/zadania/"' in html
    _assert_clean(html)
    # Obie tabele w kolejności strony; kolumny jak ``cms/blocks/schedule.html``.
    assert html.index("<caption>Jesień</caption>") < html.index("<caption>Wiosna</caption>")
    assert '<th scope="col">Temat</th>' in html and '<th scope="col">Kiedy</th>' in html
    assert "Kubity &lt;b&gt;1&lt;/b&gt;" in html  # napis z API – autoescape
    jesien = html.split("<caption>Jesień</caption>", 1)[1].split("</table>", 1)[0]
    assert '<td class="schedule__time">10:00</td>' in jesien
    assert "schedule__lecturer" not in jesien  # pusta kolumna znika (liczone z wierszy, nie z API)
    wiosna = html.split("<caption>Wiosna</caption>", 1)[1].split("</table>", 1)[0]
    assert '<td class="schedule__lecturer">dr Y</td>' in wiosna and "schedule__time" not in wiosna
    assert "Stała treść" in html


def test_workshops_page_missing_in_wagtail_renders_empty_state(client, api_up, workshops_page):
    """Nowy konkurs bez strony „Warsztaty” w Wagtailu: ``page`` = ``null`` – pusty stan, nie błąd."""
    api_up.set("workshops", workshops_payload(page=None, schedules=[], page_path=None))
    html = _html(client.get("/warsztaty/"))
    assert "Harmonogram warsztatów zostanie opublikowany wkrótce." in html
    assert "doc-intro" not in html  # wprowadzenie puste – bez opakowania
    assert UNAVAILABLE not in html


def test_workshops_api_dead_shows_unavailable(client, api_up, workshops_page):
    api_up.fail("workshops", urllib.error.URLError(TimeoutError()))
    html = _html(client.get("/warsztaty/"))
    assert UNAVAILABLE in html
    assert "Jesień" not in html


def test_workshops_intro_links_get_competition_prefix(client, api_up, make_competition, superuser):
    """Konkurs pod prefiksem ``/druga/``: odnośnik ``/zadania/`` z tekstu Wagtaila → ``/druga/zadania/``."""
    from apps.sites.models import CompetitionSite

    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    druga = make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://testserver",
        public_path_prefix="/druga",
    )
    _published_page(
        "Warsztaty", "warsztaty", druga.site, intro=[("WorkshopSchedulePlugin", {"part": "intro"})]
    )
    api_up.set("chrome", api_up_chrome(), competition="druga")
    api_up.set("workshops", workshops_payload(), competition="druga")
    html = _html(client.get("/druga/warsztaty/"))
    assert 'href="/druga/zadania/"' in html
    _assert_clean(html)


def api_up_chrome():
    from conftest import _chrome_payload

    return _chrome_payload()


def _published_page(title, slug, site, template="dj/pages/content.html", **slots):
    """Opublikowana strona w **wskazanej** witrynie z wtyczkami w slotach."""
    from cms.api import add_plugin, create_page
    from cms.models import PageContent
    from django.contrib.auth import get_user_model
    from djangocms_versioning.models import Version

    user = get_user_model().objects.filter(is_superuser=True).first()
    page = create_page(title, template, "pl", slug=slug, created_by=user, site=site, in_navigation=True)
    content = PageContent.admin_manager.get(page=page, language="pl")
    placeholders = content.rescan_placeholders()
    for slot, plugins in slots.items():
        for plugin_type, fields in plugins:
            add_plugin(placeholders[slot], plugin_type, "pl", **fields)
    Version.objects.get_for_content(content).publish(user)
    return page


# --- partnerzy ------------------------------------------------------------------------------------


def partners_payload(**overrides) -> dict:
    payload = {
        "page_path": "/partnerzy/",
        "levels": [["patron-honorowy", "patron honorowy"], ["partner-naukowy", "partner naukowy"]],
        "partners": [
            {
                "name": "Instytut B",
                "level": "partner-naukowy",
                "logo": {"src": "https://s3.olimpiada.example/b.png", "width": 320, "height": 100},
                "url": "https://instytut.example",
                "description": "Opis <i>B</i>",
                "initials": "IB",
                "is_wide": True,
            },
            {
                "name": "Patron A",
                "level": "patron-honorowy",
                "logo": {"src": "javascript:alert(1)", "width": 1, "height": 1},
                "url": "javascript:alert(2)",
                "description": "",
                "initials": "PA",
                "is_wide": False,
            },
            {
                "name": "Nieznany",
                "level": "mecenas",
                "logo": None,
                "url": "",
                "description": "",
                "initials": "N",
            },
        ],
        "page": {
            "title": "Partnerzy",
            "intro": HOSTILE_HTML,
            "become_partner_title": "Zostań partnerem",
            "become_partner_body": '<p>Napisz <a href="javascript:x()">tu</a><script>alert("y")</script></p>',
            "contact_email": "partnerzy@olimpiada.example",
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def partners_page(live_page):
    return live_page(
        "Partnerzy", "partnerzy", "dj/live/partners_page.html", partners=[("PartnersLivePlugin", {})]
    )


def test_partners_come_live_grouped_by_level(client, api_up, partners_page):
    api_up.set("partners", partners_payload())
    html = _html(client.get("/partnerzy/"))
    # Grupy w kolejności ``levels``, nie kolejności wpisów; poziom spoza słownika – bez grupy.
    assert html.index("Patron honorowy") < html.index("Partner naukowy")
    assert "Nieznany" not in html
    assert '<li class="partner--wide">' in html
    assert 'src="https://s3.olimpiada.example/b.png"' in html
    assert 'href="https://instytut.example"' in html
    # Zły adres i zły logotyp: karta bez odnośnika i z inicjałami.
    patron = html.split("Patron honorowy", 1)[1].split("</section>", 1)[0]
    assert "partner-card__link" not in patron and ">PA</span>" in patron
    assert "Opis &lt;i&gt;B&lt;/i&gt;" in html
    # Tekst formatowany – po sanityzatorze.
    assert "Wstęp <strong>ważny</strong>" in html
    assert "Zostań partnerem" in html and 'href="mailto:partnerzy@olimpiada.example"' in html
    assert 'alert("y")' not in html
    _assert_clean(html)


def test_partners_bad_email_and_missing_cta(client, api_up, partners_page):
    payload = partners_payload()
    payload["page"]["contact_email"] = 'x" onmouseover="alert(1)'
    api_up.set("partners", payload)
    main = _html(client.get("/partnerzy/")).split("<main", 1)[1].split("</main>", 1)[0]
    assert "mailto:" not in main and "Zostań partnerem" in main and "onmouseover" not in main
    payload["page"]["become_partner_body"] = ""
    api_up.set("partners", payload)
    from django.core.cache import cache

    cache.clear()
    assert "Zostań partnerem" not in _html(client.get("/partnerzy/"))


def test_partners_page_empty_and_missing(client, api_up, partners_page):
    api_up.set("partners", partners_payload(partners=[]))
    assert "Lista partnerów I edycji zostanie opublikowana wkrótce." in _html(client.get("/partnerzy/"))
    from django.core.cache import cache

    cache.clear()
    api_up.set("partners", {"page_path": None, "levels": [], "partners": [], "page": None})
    html = _html(client.get("/partnerzy/"))
    assert "Lista partnerów zostanie opublikowana wkrótce." in html
    assert "Zostań partnerem" not in html


def test_partners_api_dead(client, api_up, partners_page):
    api_up.fail("partners", urllib.error.URLError(TimeoutError()))
    html = _html(client.get("/partnerzy/"))
    assert UNAVAILABLE in html


def test_home_partners_strip_uses_live_partners(client, api_up, live_page, partners_page):
    from apps.live.homepage import partners_strip

    api_up.set("partners", partners_payload())
    # Bez żądania (brak konkursu) – brak danych, sekcja znika.
    from django.contrib.sites.models import Site

    assert partners_strip(Site.objects.get(pk=1), None) is None
    from django.test import RequestFactory

    request = RequestFactory().get("/", HTTP_HOST="testserver")
    from apps.sites.models import CompetitionSite

    request.competition_site = CompetitionSite.objects.get(slug="kwantowa")
    strip = partners_strip(Site.objects.get(pk=1), request)
    assert strip["url"] == "/partnerzy/"
    assert [entry["name"] for entry in strip["entries"]] == ["Instytut B", "Patron A", "Nieznany"]
    assert strip["entries"][1]["url"] == "" and strip["entries"][1]["logo"] is None


# --- funkcje pomocnicze ---------------------------------------------------------------------------


def test_rich_text_sanitizes_and_prefixes(settings):
    from django.urls import set_script_prefix

    html = data.rich_text(HOSTILE_HTML)
    assert "<strong>ważny</strong>" in html and "<script" not in html and "onerror" not in html
    assert "javascript:" not in html and "onclick" not in html
    assert data.rich_text(None) == "" and data.rich_text(5) == ""
    set_script_prefix("/druga/")
    try:
        assert 'href="/druga/zadania/"' in data.rich_text('<a href="/zadania/">z</a>')
        assert "/druga//evil" not in data.rich_text('<a href="//evil.example/">z</a>')
        assert 'href="https://x.example/"' in data.rich_text('<a href="https://x.example/">z</a>')
    finally:
        set_script_prefix("/")


def test_prefix_links_leaves_absolute_and_anchor_links():
    source = '<a href="/a/">1</a><a href=\'/b/\'>2</a><a href="#c">3</a><a href="//h/">4</a><a href="https://h/">5</a>'
    out = data.prefix_links(source, "/druga")
    assert 'href="/druga/a/"' in out and "href='/druga/b/'" in out
    assert 'href="#c"' in out and 'href="//h/"' in out and 'href="https://h/"' in out
    assert data.prefix_links(source, "/") == source


@pytest.mark.parametrize(
    "value",
    [
        "/\t/evil.example",
        "/\n/evil.example",
        "/ /evil.example",
        "/x\x00y",
        "https://ok.example/\tx",
        "/a\x7fb",
    ],
)
def test_safe_href_rejects_whitespace_and_control_characters(value):
    assert safe_href(value) == ""


def test_safe_href_still_accepts_normal_addresses():
    assert safe_href("  /zadania/  ") == "/zadania/"
    assert safe_href("https://olimpiada.example/a%20b") == "https://olimpiada.example/a%20b"
    assert safe_href("//evil.example") == ""
