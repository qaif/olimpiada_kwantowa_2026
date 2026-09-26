"""CSP i noindex djcms (reguły 6 i 10 z § 7 docs/tasks/DJ-01.md, § 8.4).

Publiczna polityka ma być co najmniej tak szczelna jak na stronie głównej: nonce +
``'strict-dynamic'``, **bez** ``'unsafe-inline'``/``'unsafe-eval'`` w ``script-src``. Luźna polityka
wyłącznie dla ``/admin/`` i zalogowanego personelu.
"""

import re

import pytest
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path

ROBOTS = "noindex, nofollow, noarchive"
META_ROBOTS = '<meta name="robots" content="noindex, nofollow">'


def _directives(response) -> dict[str, list[str]]:
    policy = response["Content-Security-Policy"]
    result = {}
    for part in policy.split(";"):
        name, *values = part.split()
        result[name] = values
    return result


def _assert_public_policy(response):
    csp = _directives(response)
    script = csp["script-src"]
    assert "'unsafe-inline'" not in script
    assert "'unsafe-eval'" not in script
    assert "'strict-dynamic'" in script
    nonces = [value for value in script if value.startswith("'nonce-")]
    assert len(nonces) == 1
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["object-src"] == ["'none'"]
    assert csp["base-uri"] == ["'self'"]
    return nonces[0][len("'nonce-") : -1]


def _assert_editor_policy(response):
    csp = _directives(response)
    assert "'unsafe-inline'" in csp["script-src"]
    # Nonce w polityce panelu wyłączyłby 'unsafe-inline' – panel byłby martwy.
    assert not any(value.startswith("'nonce-") for value in csp["script-src"])
    assert csp["frame-ancestors"] == ["'self'"]
    cache_control = response["Cache-Control"]
    assert "private" in cache_control
    assert "no-store" in cache_control


# --- publiczne strony --------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_page_has_strict_csp_and_noindex(client, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    nonce = _assert_public_policy(response)
    assert response["X-Robots-Tag"] == ROBOTS
    html = response.content.decode()
    assert META_ROBOTS in html
    # Każdy <script> w publicznym HTML ma nonce z nagłówka (dziś: żaden skrypt anonimowy –
    # pasek narzędzi nie jest renderowany; rama z DJ-01d dołoży własne, test zostaje ten sam).
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert f'nonce="{nonce}"' in tag, tag


@pytest.mark.django_db
def test_nonce_is_fresh_per_request(client, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    first = _assert_public_policy(client.get("/o-olimpiadzie/"))
    second = _assert_public_policy(client.get("/o-olimpiadzie/"))
    assert first != second


@pytest.mark.django_db
def test_public_policy_lists_main_media_origin_and_embed_hosts(client, settings):
    settings.DJCMS_MAIN_MEDIA_ORIGIN = "https://s3.olimpiada.example/public-media/"
    csp = _directives(client.get("/robots.txt"))
    assert csp["img-src"] == ["'self'", "data:", "https://s3.olimpiada.example"]
    assert csp["media-src"] == ["'self'", "https://s3.olimpiada.example"]
    assert csp["frame-src"] == [
        "https://www.youtube.com",
        "https://www.youtube-nocookie.com",
        "https://player.vimeo.com",
    ]


@pytest.mark.django_db
def test_empty_media_origin_is_omitted(client, settings):
    settings.DJCMS_MAIN_MEDIA_ORIGIN = ""
    csp = _directives(client.get("/robots.txt"))
    assert csp["img-src"] == ["'self'", "data:"]
    assert csp["media-src"] == ["'self'"]


@pytest.mark.django_db
def test_404_has_noindex_header_meta_and_strict_csp(client):
    response = client.get("/nie-ma-takiej-strony/")
    assert response.status_code == 404
    assert response["X-Robots-Tag"] == ROBOTS
    assert META_ROBOTS in response.content.decode()
    _assert_public_policy(response)


def _boom(request):
    raise RuntimeError("awaria testowa")


def _ok(request):
    return HttpResponse("ok")


urlpatterns = [path("boom/", _boom), path("ok/", _ok)]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__)
def test_500_has_noindex_header_and_meta(client):
    client.raise_request_exception = False
    response = client.get("/boom/")
    assert response.status_code == 500
    assert response["X-Robots-Tag"] == ROBOTS
    assert META_ROBOTS in response.content.decode()
    assert "Content-Security-Policy" in response


# --- panel i personel ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_login_gets_editor_policy_and_noindex(client):
    response = client.get("/djcms/admin/login/")
    assert response.status_code == 200
    _assert_editor_policy(response)
    assert response["X-Robots-Tag"] == ROBOTS


@pytest.mark.django_db
def test_admin_404_still_editor_policy(client, superuser):
    client.force_login(superuser)
    response = client.get("/djcms/admin/nie-ma-takiego-widoku/")
    assert response.status_code == 404
    _assert_editor_policy(response)
    assert response["X-Robots-Tag"] == ROBOTS


@pytest.mark.django_db
def test_staff_on_public_page_gets_editor_policy(client, make_page, editor):
    make_page("O olimpiadzie", "o-olimpiadzie")
    client.force_login(editor)
    response = client.get("/o-olimpiadzie/")
    assert response.status_code == 200
    _assert_editor_policy(response)
    assert response["X-Robots-Tag"] == ROBOTS


@pytest.mark.django_db
def test_logged_in_non_staff_gets_public_policy(client, make_page, django_user_model):
    make_page("O olimpiadzie", "o-olimpiadzie")
    user = django_user_model.objects.create_user(username="zwykly", password="haslo-zwyklego-123")
    client.force_login(user)
    _assert_public_policy(client.get("/o-olimpiadzie/"))


@pytest.mark.django_db
def test_anonymous_toolbar_is_not_rendered(client, make_page):
    # CMS_TOOLBAR_ANONYMOUS_ON = False: ``?toolbar_on`` nie daje anonimowi paska (jego skrypty
    # inline nie przeszłyby przez ścisłą politykę, a panel logowania jest pod /admin/).
    make_page("O olimpiadzie", "o-olimpiadzie")
    html = client.get("/o-olimpiadzie/?toolbar_on").content.decode()
    assert "cms-toolbar" not in html
