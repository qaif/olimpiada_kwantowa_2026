"""Tryb ``preview``/``primary`` z nagłówka Caddy'ego (DJ-02 D10, S7) i nagłówki odpowiedzi.

``X-Djcms-Mode: primary`` zdejmuje noindex **wyłącznie** od zaufanego proxy (``TRUSTED_PROXY_IPS``
– w testach ``172.30.1.0/24``; klient testowy Django łączy się z ``127.0.0.1``). Każdy inny
przypadek to ``preview`` – bezpieczny kierunek błędu.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.pages import mode

ROBOTS = "noindex, nofollow, noarchive"
META_ROBOTS = '<meta name="robots" content="noindex, nofollow">'
PROXY = "172.30.1.5"
PRIMARY = {"HTTP_X_DJCMS_MODE": "primary", "REMOTE_ADDR": PROXY}


@pytest.fixture
def page(make_page):
    return make_page("O olimpiadzie", "o-olimpiadzie")


# --- zaufanie do nagłówka --------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_header_is_preview_with_noindex(client, page):
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    assert response[mode.HEADER] == "preview"
    assert response["X-Robots-Tag"] == ROBOTS
    assert META_ROBOTS in response.content.decode()


@pytest.mark.django_db
def test_spoofed_primary_from_untrusted_address_is_ignored(client, page):
    # Klient z internetu (albo cokolwiek spoza TRUSTED_PROXY_IPS) wpisuje nagłówek sam.
    response = client.get("/o-olimpiadzie/", HTTP_X_DJCMS_MODE="primary", REMOTE_ADDR="203.0.113.9")
    assert response[mode.HEADER] == "preview"
    assert response["X-Robots-Tag"] == ROBOTS
    assert META_ROBOTS in response.content.decode()


@pytest.mark.django_db
def test_primary_from_trusted_proxy_drops_noindex(client, page):
    response = client.get("/o-olimpiadzie/", **PRIMARY)
    assert response.status_code == 200
    assert response[mode.HEADER] == "primary"
    assert "X-Robots-Tag" not in response
    html = response.content.decode()
    assert 'name="robots"' not in html
    # Stopka jak w Wagtailu: numer wersji zamiast dopisku o podglądzie; bez paska podglądu.
    assert '<p class="footer__meta">wersja dev</p>' in html
    assert "data-djcms-preview-bar" not in html
    assert "nieindeksowany" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["preview", "", "PRIMARY-ish", "primary, primary"])
def test_trusted_proxy_other_values_are_preview(client, page, value):
    response = client.get("/o-olimpiadzie/", HTTP_X_DJCMS_MODE=value, REMOTE_ADDR=PROXY)
    assert response[mode.HEADER] == "preview"
    assert response["X-Robots-Tag"] == ROBOTS


@pytest.mark.django_db
def test_empty_trusted_proxy_list_trusts_nobody(client, page, settings):
    settings.TRUSTED_PROXY_IPS = []
    response = client.get("/o-olimpiadzie/", **PRIMARY)
    assert response[mode.HEADER] == "preview"


@pytest.mark.django_db
def test_app_paths_keep_noindex_in_primary(client):
    for path in ("/djcms/admin/login/", "/djcms/preview/", "/djcms/healthz/"):
        response = client.get(path, **PRIMARY)
        assert response[mode.HEADER] == "primary"
        assert response["X-Robots-Tag"] == ROBOTS, path


@pytest.mark.django_db
def test_unknown_host_empty_404_still_carries_mode_and_noindex(client):
    response = client.get("/", HTTP_HOST="nieznany.olimpiada.example")
    assert response.status_code == 404
    assert response.content == b""
    assert response[mode.HEADER] == "preview"
    assert response["X-Robots-Tag"] == ROBOTS


# --- nagłówki bufora -------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("meta", [{}, PRIMARY])
def test_public_html_is_private_no_store(client, page, meta):
    cache_control = client.get("/o-olimpiadzie/", **meta)["Cache-Control"]
    assert "private" in cache_control
    assert "no-store" in cache_control
    assert "public" not in cache_control


@pytest.mark.django_db
def test_404_html_is_private_no_store(client):
    response = client.get("/nie-ma-takiej-strony/", **PRIMARY)
    assert response.status_code == 404
    assert "no-store" in response["Cache-Control"]
    # Strona błędu nie jest treścią – meta noindex także w ``primary``.
    assert META_ROBOTS in response.content.decode()


# --- pomocnik dla verify_cutover -------------------------------------------------------------------


def test_proxy_request_meta_uses_a_trusted_address(settings):
    settings.TRUSTED_PROXY_IPS = ["nie-adres", "172.30.1.0/24"]
    meta = mode.proxy_request_meta()
    assert meta == {"HTTP_X_DJCMS_MODE": "primary", "REMOTE_ADDR": "172.30.1.1"}
    settings.TRUSTED_PROXY_IPS = ["10.1.2.3"]
    assert mode.proxy_request_meta("preview")["REMOTE_ADDR"] == "10.1.2.3"


def test_proxy_request_meta_refuses_without_trusted_proxies(settings):
    settings.TRUSTED_PROXY_IPS = []
    with pytest.raises(ImproperlyConfigured):
        mode.proxy_request_meta()
    with pytest.raises(ValueError):
        mode.proxy_request_meta("inny")


@pytest.mark.django_db
def test_proxy_request_meta_really_switches_the_mode(client, page):
    response = client.get("/o-olimpiadzie/", **mode.proxy_request_meta())
    assert response[mode.HEADER] == "primary"
    assert "X-Robots-Tag" not in response
