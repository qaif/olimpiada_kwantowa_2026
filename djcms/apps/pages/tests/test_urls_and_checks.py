"""Reguła 5 z § 7 docs/tasks/DJ-01.md: redaktor nie przesłoni adresu aplikacji.

Trzy warstwy: kolejność urlconfu (adresy aplikacji przed ``cms.urls``), lista
``DJ_RESERVED_SLUGS`` i system check ``dj_pages.W001`` dla stron, które mimo to taki adres mają.
"""

import pytest
from django.conf import settings
from django.urls import URLResolver, resolve

from apps.pages import checks, views
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


def test_app_urls_resolve_to_app_views():
    assert resolve("/healthz/").func is views.healthz
    assert resolve("/robots.txt").func is views.robots_txt
    assert resolve("/admin/").namespace == "admin"


def test_reserved_slugs_cover_every_app_prefix():
    # Każdy pierwszy segment adresu aplikacji musi być zarezerwowany – strona o takim slugu
    # byłaby niewidoczna (albo, bez kolejności urlconfu, przesłaniałaby aplikację).
    for prefix in ("admin", "healthz", "robots.txt", "static", "media", "internal", "filer"):
        assert prefix in settings.DJ_RESERVED_SLUGS


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["healthz", "admin"])
def test_page_with_reserved_slug_does_not_shadow_app(client, make_page, slug):
    make_page(f"Strona {slug}", slug)
    assert resolve(f"/{slug}/").url_name != "pages-details-by-slug"
    if slug == "healthz":
        assert client.get("/healthz/").json()["status"] == "ok"


@pytest.mark.django_db
def test_check_w001_reports_published_reserved_page(make_page):
    page = make_page("Healthz", "healthz")
    warnings = checks.reserved_slug_pages(databases=["default"])
    assert [w.id for w in warnings] == ["dj_pages.W001"]
    assert f"id={page.pk}" in warnings[0].msg
    assert "/healthz/" in warnings[0].msg


@pytest.mark.django_db
def test_check_w001_ignores_drafts_and_normal_slugs(make_page):
    make_page("Szkic", "admin", publish=False)
    make_page("Zwykła", "o-olimpiadzie")
    assert checks.reserved_slug_pages(databases=["default"]) == []


def test_check_w001_skipped_without_database():
    # Zwykły ``manage.py check`` i collectstatic w czasie budowania obrazu – bez bazy.
    assert checks.reserved_slug_pages(databases=None) == []
