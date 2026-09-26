"""Reguła 5 z § 7 DJ-01 i S5/S6 z § 7 DJ-02: strona djcms nie przesłoni adresu aplikacji.

Warstwy: kolejność urlconfu (adresy djcms pod ``/djcms/`` przed ``cms.urls``), kontrakt tras
aplikacji głównej (``app_routes.json`` → ``DJ_RESERVED_SLUGS`` i ``apps.pages.validation``),
walidacja w formularzach stron django CMS i system check ``dj_pages.W001`` dla stron, które mimo
to taki adres mają.
"""

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import URLResolver, resolve

from apps.pages import checks, validation, views
from config import urls


def test_cms_catch_all_is_last():
    last = urls.urlpatterns[-1]
    assert isinstance(last, URLResolver)
    assert last.urlconf_module.__name__ == "cms.urls"
    # Nic po nim – każdy wzorzec za catch-allem byłby martwy albo (gorzej) przesłonięty.
    cms_positions = [
        i
        for i, pattern in enumerate(urls.urlpatterns)
        if isinstance(pattern, URLResolver) and getattr(pattern.urlconf_module, "__name__", "") == "cms.urls"
    ]
    assert cms_positions == [len(urls.urlpatterns) - 1]


def test_app_urls_live_under_djcms_prefix():
    assert resolve("/djcms/healthz/").func is views.healthz
    assert resolve("/djcms/admin/").namespace == "admin"
    assert resolve("/djcms/preview/").url_name == "preview"
    assert resolve("/djcms/sso/").url_name == "sso"
    assert resolve("/djcms/preview/").func is views.preview_toggle
    assert resolve("/robots.txt").func is views.robots_txt
    assert resolve("/sitemap.xml").func is views.sitemap_xml
    # Stare adresy DJ-01 nie należą już do djcms – pod nimi jest aplikacja główna (Caddy).
    for old in ("/admin/", "/healthz/"):
        assert resolve(old).url_name == "pages-details-by-slug"
    assert settings.STATIC_URL == "/djcms/static/"
    assert settings.MEDIA_URL == "/djcms/media/"


@pytest.mark.django_db
def test_sso_is_a_reserved_placeholder(client):
    response = client.get("/djcms/sso/")
    assert response.status_code == 404
    assert response.content == b""


def test_reserved_slugs_cover_own_prefixes_and_every_app_segment():
    for own in ("djcms", "robots.txt", "sitemap.xml", "static", "media"):
        assert own in settings.DJ_RESERVED_SLUGS
    assert set(settings.DJ_APP_ROUTES["first_segments"]) <= settings.DJ_RESERVED_SLUGS
    # Przykłady z kontraktu – adresy aplikacji głównej, które Caddy kieruje do ``web``.
    for app in ("admin", "login", "me", "cms", "api", "healthz", "internal", "documents"):
        assert app in settings.DJ_RESERVED_SLUGS


@pytest.mark.parametrize(
    "path",
    [
        "admin",
        "login",
        "djcms",
        "static",
        "robots.txt",
        "cms/strona",
        "warsztaty/materialy",
        "warsztaty/materialy/plik",
        "o-nas/login",
        "o-nas/me/x",
        "status.json",
    ],
)
def test_app_paths_collide(path):
    assert validation.path_collides_with_app(path)
    with pytest.raises(ValidationError):
        validation.validate_page_path(path)


@pytest.mark.parametrize(
    "path", ["", "zadania", "warsztaty", "dokumenty/regulamin", "o-nas/logowanie", "wyniki", "a/b/login"]
)
def test_page_paths_do_not_collide(path):
    assert validation.path_collides_with_app(path) is None
    validation.validate_page_path(path)


@pytest.mark.django_db
def test_page_with_reserved_slug_does_not_shadow_djcms(client, make_page):
    make_page("Strona djcms", "djcms")
    assert resolve("/djcms/healthz/").func is views.healthz
    assert client.get("/djcms/healthz/").json()["status"] == "ok"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("slug", "parent_slug"),
    [("admin", None), ("login", None), ("login", "o-nas"), ("materialy", "warsztaty")],
)
def test_check_w001_reports_published_app_paths(make_page, slug, parent_slug):
    parent = make_page(parent_slug.title(), parent_slug) if parent_slug else None
    page = make_page("Kolizja", slug, parent=parent)
    warnings = checks.reserved_slug_pages(databases=["default"])
    assert [w.id for w in warnings] == ["dj_pages.W001"]
    assert f"id={page.pk}" in warnings[0].msg
    expected = f"/{parent_slug}/{slug}/" if parent_slug else f"/{slug}/"
    assert expected in warnings[0].msg


@pytest.mark.django_db
def test_check_w001_ignores_drafts_and_normal_slugs(make_page):
    make_page("Szkic", "admin", publish=False)
    make_page("Zwykła", "o-olimpiadzie")
    assert checks.reserved_slug_pages(databases=["default"]) == []


def test_check_w001_skipped_without_database():
    # Zwykły ``manage.py check`` i collectstatic w czasie budowania obrazu – bez bazy.
    assert checks.reserved_slug_pages(databases=None) == []


# --- formularze stron django CMS (S5) --------------------------------------------------------------


def test_form_validation_is_installed_once():
    from cms.admin import forms

    assert getattr(forms.validate_url_uniqueness, validation.PATCH_MARKER, False)
    before = forms.validate_url_uniqueness
    validation.install_form_validation()
    assert forms.validate_url_uniqueness is before


@pytest.mark.django_db
def test_add_page_form_rejects_app_path(client, superuser):
    client.force_login(superuser)
    response = client.post(
        "/djcms/admin/cms/pagecontent/add/?language=pl",
        {"title": "Logowanie", "slug": "login", "template": "dj/pages/content.html"},
    )
    assert response.status_code == 200  # formularz wraca z błędem, strona nie powstała
    assert "należy do aplikacji" in response.content.decode() or "zarezerwowany" in response.content.decode()
    from cms.models import Page

    assert not Page.objects.exists()


@pytest.mark.django_db
def test_add_page_form_accepts_normal_path(client, superuser):
    client.force_login(superuser)
    response = client.post(
        "/djcms/admin/cms/pagecontent/add/?language=pl",
        {"title": "O nas", "slug": "o-nas", "template": "dj/pages/content.html"},
    )
    assert response.status_code == 302
    from cms.models import Page

    assert Page.objects.get().site_id == 1


@pytest.mark.django_db
def test_change_page_form_rejects_slug_changed_to_app_path(client, superuser, make_page):
    from cms.models import PageContent

    page = make_page("O nas", "o-nas", publish=False)
    content = PageContent.admin_manager.get(page=page, language="pl")
    before = list(page.urls.values_list("slug", "path"))
    client.force_login(superuser)
    response = client.post(
        f"/djcms/admin/cms/pagecontent/{content.pk}/change/?language=pl",
        {"title": "O nas", "slug": "coordinator", "template": "dj/pages/content.html"},
    )
    assert response.status_code == 200  # formularz z błędem zamiast przekierowania po zapisie
    assert "należy do aplikacji" in response.content.decode() or "zarezerwowany" in response.content.decode()
    assert list(page.urls.values_list("slug", "path")) == before
