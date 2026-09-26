"""Rama serwisu ``dj.`` – ``dj/base.html`` karmiony z API ``chrome`` (§ 8.3, 8.5 docs/tasks/DJ-01.md).

Sprawdzamy trzy stany: API odpowiada (pełna rama jak w Wagtailu), API martwe (rama zostaje,
znika to, czego nie da się pokazać bez danych; 200 + ``X-Djcms-Degraded: 1``) i kopia „stale”.
Do tego reguła 6: każdy ``<script>`` publicznej strony ma nonce z nagłówka CSP – przy pełnej ramie,
czyli z kompletem skryptów.
"""

import re

import pytest
from django.core.cache import cache

from apps.live import client as api_client

DEGRADED = "X-Djcms-Degraded"
#: Skrypty ramy – ta sama lista i kolejność co w ``backend/templates/base.html``, bez GA/HTMX/Alpine.
FRAME_SCRIPTS = [
    "/static/js/app.js",
    "/static/js/consent.js",
    "/static/js/sticky-bar.js",
    "/static/js/table-scroll.js",
    "/static/js/timeline-strip.js",
    "/static/js/announcements.js",
    "/static/js/sponsor-slider.js",
]


def _nonce(response) -> str:
    return re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)


@pytest.fixture
def page(make_page):
    return make_page("O olimpiadzie", "o-olimpiadzie")


@pytest.fixture
def full_chrome(main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    return main_api


# --- API działa ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_full_frame_from_chrome(client, page, full_chrome):
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    assert DEGRADED not in response
    html = response.content.decode()
    assert "<title>O olimpiadzie – Olimpiada Testowa</title>" in html
    assert '<a class="skip-link" href="#tresc">' in html
    assert '<main class="page" id="tresc">' in html
    assert '<span class="brand__sub">I edycja 2026/2027</span>' in html
    assert '<span class="brand__mark visually-hidden">Olimpiada Testowa</span>' in html
    # Pasek konta: zawsze anonimowy, adresy na domenę główną.
    assert (
        '<a class="account-bar__link" href="https://olimpiada.example/support/new/">Zgłoś problem</a>' in html
    )
    assert 'href="https://olimpiada.example/login/">Zaloguj się</a>' in html
    assert 'href="https://olimpiada.example/register/">Zarejestruj się</a>' in html
    # Komunikat: tekst autoescapowany, odnośnik osobnym polem.
    assert 'class="announcement announcement--warning"' in html
    assert 'data-announcement="4"' in html
    assert "Uwaga &lt;b&gt;ważne&lt;/b&gt;" in html
    assert '<a class="announcement__link" href="https://olimpiada.example/x">Więcej</a>' in html
    # Slider, pasek osi czasu, stopka.
    assert 'data-sponsor-slider data-interval="5"' in html
    assert 'src="https://s3.olimpiada.example/a.png" alt="Sponsor A"' in html
    assert 'data-axis-start="2026-09-01"' in html and 'data-axis-end="2027-06-30"' in html
    assert 'href="/harmonogram/" data-tl-cell="1"' in html
    assert '<button type="button" class="tl__cell tl__mark" data-tl-cell="2"' in html
    assert (
        '<span class="footer__logo"><img src="https://s3.olimpiada.example/logo.png" width="240" height="80"'
        in html
    )
    assert "Organizator: <strong>Fundacja Testowa</strong>" in html
    assert 'href="https://facebook.com/x" aria-label="Facebook"' in html
    assert '<a href="tel:+48500600700">' in html
    assert '<a href="https://olimpiada.example/plakaty/">Plakaty do pobrania</a>' in html
    assert '<a href="https://olimpiada.example/o-olimpiadzie/">Ta strona w wersji Wagtail</a>' in html
    assert "Wersja testowa (django CMS) – nieindeksowana" in html
    assert '<a href="/dokumenty/rodo/">' in html


@pytest.mark.django_db
def test_every_script_has_the_response_nonce(client, page, full_chrome):
    response = client.get("/o-olimpiadzie/")
    html = response.content.decode()
    nonce = _nonce(response)
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags, "rama ma ładować skrypty – inaczej test niczego nie sprawdza"
    for tag in tags:
        assert f'nonce="{nonce}"' in tag, tag
    assert re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html) == FRAME_SCRIPTS
    for forbidden in ("htmx", "alpinejs", "googletagmanager", "analytics.js"):
        assert forbidden not in html
    assert "hx-headers" not in html  # bez HTMX nie ma po co wystawiać tokenu CSRF w <body>


@pytest.mark.django_db
def test_shared_stylesheet_and_meta(client, page, full_chrome):
    html = client.get("/o-olimpiadzie/").content.decode()
    assert '<link rel="stylesheet" href="/static/css/app.css">' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert '<meta property="og:site_name" content="Olimpiada Testowa">' in html


@pytest.mark.django_db
def test_chrome_is_fetched_once_per_page(client, page, full_chrome):
    client.get("/o-olimpiadzie/")
    assert full_chrome.calls("chrome") == 1
    cache.clear()  # nowa odsłona bez bufora procesu = nowe pobranie, ale znów jedno
    client.get("/o-olimpiadzie/")
    assert full_chrome.calls("chrome") == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("registration", "expected", "absent"),
    [
        (
            {"is_open": False, "reason": "not_yet", "opens_at_display": "8 września 2026"},
            'href="https://olimpiada.example/register/">Rejestracja rusza 8 września 2026</a>',
            "Zarejestruj się",
        ),
        ({"is_open": False, "reason": "closed", "opens_at_display": ""}, None, '/register/">'),
        ({"is_open": False, "reason": "disabled", "opens_at_display": ""}, None, '/register/">'),
    ],
)
def test_registration_button_follows_state(
    client, page, main_api, chrome_payload, registration, expected, absent
):
    main_api.set("chrome", chrome_payload(registration=registration))
    html = client.get("/o-olimpiadzie/").content.decode()
    if expected:
        assert expected in html
    assert absent not in html


@pytest.mark.django_db
def test_unsafe_urls_from_api_are_dropped(client, page, main_api, chrome_payload):
    payload = chrome_payload()
    payload["announcements"][0]["link_url"] = "javascript:alert(1)"
    payload["site"]["contact_url"] = "javascript:alert(2)"
    payload["links"]["support"] = "//evil.example/x"
    payload["sponsor_slider"]["entries"][0]["url"] = "data:text/html,x"
    main_api.set("chrome", payload)
    html = client.get("/o-olimpiadzie/").content.decode()
    assert "javascript:" not in html
    assert "evil.example" not in html
    assert "data:text/html" not in html
    assert "announcement__link" not in html  # komunikat został, bez odnośnika
    assert "Uwaga &lt;b&gt;ważne&lt;/b&gt;" in html


# --- API martwe i kopia „stale” ----------------------------------------------------------------


@pytest.mark.django_db
def test_dead_api_keeps_frame_and_marks_degraded(client, page, main_api, settings):
    # ``main_api`` bez ustawionych odpowiedzi = błąd połączenia na każde żądanie.
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    assert response[DEGRADED] == "1"
    html = response.content.decode()
    assert f"<title>O olimpiadzie – {settings.DJCMS_FALLBACK_SITE_NAME}</title>" in html
    assert '<nav class="account-bar"' in html
    assert 'href="https://olimpiada.example/login/">Zaloguj się</a>' in html
    for missing in (
        "Zarejestruj się",
        "Rejestracja rusza",
        "Zgłoś problem",
        "announcement ",
        "data-sponsor-slider",
        "data-timeline-strip",
        "Organizator:",
        "Dla szkół/nauczycieli",
        "brand__sub",
    ):
        assert missing not in html, missing
    # Stopka i nawigacja zostają.
    assert '<footer class="footer">' in html
    assert "Ta strona w wersji Wagtail" in html
    assert '<nav class="nav nav--cms"' in html


@pytest.mark.django_db
def test_dead_api_second_page_does_not_wait_for_timeout(client, page, main_api):
    client.get("/o-olimpiadzie/")
    client.get("/o-olimpiadzie/")
    assert main_api.calls("chrome") == 1  # drugi raz bezpiecznik – bez próby połączenia


@pytest.mark.django_db
def test_stale_chrome_is_used_and_marked(client, page, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    client.get("/o-olimpiadzie/")
    cache.delete(api_client.CACHE_PREFIX + "chrome")  # świeży bufor wygasł, kopia „stale” żyje
    main_api.fail("chrome", TimeoutError())
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    assert response[DEGRADED] == "1"
    assert "Fundacja Testowa" in response.content.decode()


# --- miejsca bez ramy nie pytają API -------------------------------------------------------------


@pytest.mark.django_db
def test_admin_does_not_call_api(client, main_api):
    assert client.get("/admin/login/").status_code == 200
    assert main_api.requests == []


@pytest.mark.django_db
def test_404_does_not_call_api_and_is_not_degraded(client, main_api):
    response = client.get("/nie-ma-takiej-strony/")
    assert response.status_code == 404
    assert main_api.requests == []
    assert DEGRADED not in response
