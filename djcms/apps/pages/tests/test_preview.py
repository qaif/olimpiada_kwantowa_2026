"""``/djcms/preview/`` – ciasteczko ``djcms_view`` (DJ-02 D1, S8) i rozłączne ciasteczka (S10).

Kontrakt z Caddym (``scripts/render_caddyfile.sh``): ``djcms_view=dj`` przy ``DJCMS_PRIMARY=0``
kieruje strony publiczne hosta do djcms, ``djcms_view=wagtail`` przy ``DJCMS_PRIMARY=1`` – do ``web``.
Atrybuty: host-only, ``Path=/``, ``HttpOnly``, ``Secure``, ``SameSite=Lax``, ``Max-Age=28800``.
"""

import pytest
from django.test import Client

from apps.pages import preview

PROXY = "172.30.1.5"
PRIMARY = {"HTTP_X_DJCMS_MODE": "primary", "REMOTE_ADDR": PROXY}
URL = "/djcms/preview/"
#: S10 – jedyne ciasteczka, jakie djcms wolno ustawić.
DJCMS_COOKIES = {"djcms_sessionid", "djcms_csrftoken", "djcms_language", "djcms_view"}


def _post(client, view, next_path=None, url=URL, **extra):
    data = {"view": view}
    if next_path is not None:
        data["next"] = next_path
    return client.post(url, data, **extra)


# --- strona ----------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_preview_page_in_preview_mode_offers_enable(client):
    response = client.get(URL, {"next": "/zadania/"})
    assert response.status_code == 200
    html = response.content.decode()
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert '<input type="hidden" name="view" value="dj">' in html
    assert '<input type="hidden" name="next" value="/zadania/">' in html
    assert "Włącz podgląd django CMS" in html
    assert "no-store" in response["Cache-Control"]
    assert response["X-Robots-Tag"] == "noindex, nofollow, noarchive"


@pytest.mark.django_db
def test_preview_page_reflects_cookie_state(client):
    client.cookies[preview.COOKIE_NAME] = "dj"
    html = client.get(URL).content.decode()
    assert "Podgląd jest włączony w tej przeglądarce." in html
    assert '<input type="hidden" name="view" value="off">' in html


@pytest.mark.django_db
def test_preview_page_in_primary_offers_wagtail_comparison(client):
    html = client.get(URL, **PRIMARY).content.decode()
    assert '<input type="hidden" name="view" value="wagtail">' in html
    assert "Pokaż wersję Wagtail" in html
    client.cookies[preview.COOKIE_NAME] = "wagtail"
    html = client.get(URL, **PRIMARY).content.decode()
    assert "Wróć do django CMS" in html


@pytest.mark.django_db
def test_spoofed_primary_does_not_change_the_page(client):
    html = client.get(URL, HTTP_X_DJCMS_MODE="primary").content.decode()
    assert "Włącz podgląd django CMS" in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unsafe", ["//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)"]
)
def test_preview_page_next_is_sanitised(client, unsafe):
    html = client.get(URL, {"next": unsafe}).content.decode()
    assert "evil.example" not in html
    assert "javascript:" not in html
    assert '<input type="hidden" name="next" value="/">' in html


@pytest.mark.django_db
def test_platform_host_lists_every_competition(client, make_competition):
    make_competition(
        "fizyka", name="Olimpiada Fizyczna", hosts=["fizyka.example"], public_origin="https://fizyka.example"
    )
    make_competition(
        "druga",
        name="Druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://olimpiada.example",
        public_path_prefix="/druga",
    )
    make_competition("bez-adresu", hosts=["bez.olimpiada.example"])  # bez adresu publicznego – pominięty
    make_competition(
        "stary", hosts=["stary.olimpiada.example"], is_active=False, public_origin="https://stary.example"
    )
    html = client.get(URL).content.decode()
    assert '<a href="https://fizyka.example/djcms/preview/">Olimpiada Fizyczna</a>' in html
    assert '<a href="https://olimpiada.example/druga/djcms/preview/">Druga</a>' in html
    assert "<strong>Olimpiada Kwantowa</strong> (ten adres)" in html
    assert "bez-adresu" not in html and "stary.example" not in html
    # Pod innym hostem – bez listy (ciasteczko jest host-only, lista należy do hosta platformy).
    other = client.get(URL, HTTP_HOST="fizyka.example").content.decode()
    assert "Wszystkie konkursy" not in other


# --- POST: ustawienie i skasowanie -----------------------------------------------------------------


@pytest.mark.django_db
def test_enable_sets_host_only_cookie_and_redirects(client):
    response = _post(client, "dj", "/zadania/?x=1")
    assert response.status_code == 302
    assert response["Location"] == "/zadania/?x=1"
    cookie = response.cookies[preview.COOKIE_NAME]
    assert cookie.value == "dj"
    assert cookie["max-age"] == 8 * 3600
    assert cookie["path"] == "/"
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["domain"] == ""


@pytest.mark.django_db
def test_wagtail_and_off(client):
    response = _post(client, "wagtail", "/", **PRIMARY)
    assert response.cookies[preview.COOKIE_NAME].value == "wagtail"
    response = _post(client, "off", "/wyniki/")
    assert response["Location"] == "/wyniki/"
    cookie = response.cookies[preview.COOKIE_NAME]
    assert cookie.value == ""
    assert cookie["max-age"] == 0
    assert cookie["path"] == "/"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unsafe",
    [
        "//evil.example/x",
        "https://evil.example/",
        "http:evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "/zadania/\r\nSet-Cookie: x=1",
        "",
        "zadania/",
    ],
)
def test_next_is_never_an_open_redirect(client, unsafe):
    response = _post(client, "dj", unsafe)
    assert response.status_code == 302
    assert response["Location"] == "/"


@pytest.mark.django_db
def test_default_next_under_path_prefix_is_the_competition_root(client, make_competition, competition_site):
    competition_site.hosts_path_prefixes = True
    competition_site.save(update_fields=["hosts_path_prefixes"])
    make_competition(
        "druga",
        routing_mode="PATH",
        path_prefix="druga",
        public_origin="https://olimpiada.example",
        public_path_prefix="/druga",
    )
    response = _post(client, "dj", url="/druga/djcms/preview/")
    assert response.status_code == 302
    assert response["Location"] == "/druga/"
    # Ciasteczko na cały host – Caddy dopasowuje je także dla /druga/*.
    assert response.cookies[preview.COOKIE_NAME]["path"] == "/"


@pytest.mark.django_db
def test_unknown_action_is_rejected(client):
    response = _post(client, "admin", "/")
    assert response.status_code == 400
    assert preview.COOKIE_NAME not in response.cookies


@pytest.mark.django_db
def test_post_requires_csrf():
    client = Client(enforce_csrf_checks=True)
    response = _post(client, "dj", "/")
    assert response.status_code == 403
    assert preview.COOKIE_NAME not in response.cookies
    # Z tokenem ze strony – działa.
    client.get(URL)
    token = client.cookies["djcms_csrftoken"].value
    response = client.post(URL, {"view": "dj", "next": "/"}, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 302


@pytest.mark.django_db
def test_other_methods_are_refused(client):
    assert client.put(URL).status_code == 405
    assert client.delete(URL).status_code == 405


# --- S10: wyłącznie ciasteczka djcms -----------------------------------------------------------------


@pytest.mark.django_db
def test_responses_set_only_djcms_cookies(client, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    names = set()
    for response in (
        client.get("/o-olimpiadzie/"),
        client.get("/o-olimpiadzie/", **PRIMARY),
        client.get(URL),
        _post(client, "dj", "/"),
        _post(client, "off", "/"),
        client.get("/robots.txt"),
    ):
        names |= set(response.cookies.keys())
    assert names
    assert names <= DJCMS_COOKIES


@pytest.mark.django_db
def test_preview_bar_posts_off_back_to_the_same_page(client, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    html = client.get("/o-olimpiadzie/?a=1").content.decode()
    assert '<form method="post" action="/djcms/preview/">' in html
    assert '<input type="hidden" name="view" value="off">' in html
    assert '<input type="hidden" name="next" value="/o-olimpiadzie/?a=1">' in html
