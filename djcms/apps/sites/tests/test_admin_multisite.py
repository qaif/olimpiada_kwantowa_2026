"""Panel i pasek narzędzi pod hostem konkursu i pod prefiksem (DJ-02 § 5.1 „Edycja z paska”).

Endpointy admina nie mają witryny w adresie – każdy bierze ją z żądania (``cms.utils.get_current_site``
→ ``request.site``). Te testy przechodzą całą drogę redaktora pod hostem, który **nie** jest
``Site.domain`` (bez łatki i warstwy: ``Site.DoesNotExist`` = 500) i pod prefiksem ścieżki:
drzewo stron, tryb edycji, podgląd, publikacja wersji (djangocms-versioning) i lista wersji.
"""

import pytest
from cms.toolbar.utils import get_object_edit_url, get_object_preview_url

from apps.sites.models import CompetitionSite

pytestmark = pytest.mark.django_db


@pytest.fixture
def fizyka(make_competition):
    return make_competition("fizyka", hosts=["fizyka.example"], public_origin="https://fizyka.example")


@pytest.fixture
def druga(make_competition):
    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    return make_competition(
        "druga", routing_mode="PATH", path_prefix="druga", public_origin="https://testserver"
    )


@pytest.fixture
def editor_client(client, superuser):
    client.force_login(superuser)
    return client


def _draft(make_page, site, title):
    from cms.models import PageContent

    page = make_page(title, "strona", publish=False, site=site)
    return page, PageContent.admin_manager.get(page=page, language="pl")


@pytest.mark.parametrize(
    ("competition", "host", "prefix"), [("fizyka", "fizyka.example", ""), ("druga", "testserver", "/druga")]
)
def test_editor_flow_under_competition_host_and_prefix(
    request, editor_client, make_page, superuser, competition, host, prefix
):
    from djangocms_versioning.models import Version

    site = request.getfixturevalue(competition).site
    _page, content = _draft(make_page, site, f"Strona {competition}")
    kwargs = {"HTTP_HOST": host}

    # Drzewo stron (lista + wiersze ładowane przez ``get-tree``): strony witryny żądania.
    assert editor_client.get(f"{prefix}/djcms/admin/cms/pagecontent/", **kwargs).status_code == 200
    tree = editor_client.get(f"{prefix}/djcms/admin/cms/pagecontent/get-tree/", **kwargs)
    assert tree.status_code == 200
    assert f"Strona {competition}" in tree.content.decode()

    # Tryb edycji i podgląd (pasek narzędzi, renderer z witryną żądania).
    for url in (get_object_edit_url(content), get_object_preview_url(content)):
        response = editor_client.get(f"{prefix}{url}", **kwargs)
        assert response.status_code == 200, url

    # Publikacja wersji i lista wersji (djangocms-versioning, filtr języków z witryny żądania).
    version = Version.objects.get_for_content(content)
    publish = editor_client.post(
        f"{prefix}/djcms/admin/djangocms_versioning/pagecontentversion/{version.pk}/publish/", **kwargs
    )
    assert publish.status_code == 302
    assert Version.objects.get(pk=version.pk).state == "published"
    listing = editor_client.get(
        f"{prefix}/djcms/admin/djangocms_versioning/pagecontentversion/",
        {"page": content.page_id},
        **kwargs,
    )
    assert listing.status_code == 200

    # Strona opublikowana odpowiada pod swoim adresem publicznym (host / prefiks).
    assert editor_client.get(f"{prefix}/strona/", **kwargs).status_code == 200


def test_page_tree_of_one_site_does_not_list_the_other(editor_client, make_page, fizyka):
    make_page("Tylko kwantowa", "tylko-kwantowa")
    make_page("Tylko fizyka", "tylko-fizyka", site=fizyka.site)
    html = editor_client.get(
        "/djcms/admin/cms/pagecontent/get-tree/", HTTP_HOST="fizyka.example"
    ).content.decode()
    assert "Tylko fizyka" in html
    assert "Tylko kwantowa" not in html


def test_add_page_in_admin_lands_in_the_site_of_the_host(editor_client, fizyka):
    from cms.models import Page

    response = editor_client.post(
        "/djcms/admin/cms/pagecontent/add/?language=pl",
        {"title": "Nowa w fizyce", "slug": "nowa", "template": "dj/pages/content.html"},
        HTTP_HOST="fizyka.example",
    )
    assert response.status_code == 302
    assert Page.objects.get().site_id == fizyka.site_id


def test_admin_redirects_stay_under_the_prefix(client, druga):
    response = client.get("/druga/djcms/admin/", HTTP_HOST="testserver")
    assert response.status_code == 302
    assert response["Location"].startswith("/druga/djcms/admin/login/")


def test_placeholder_endpoints_resolve_under_prefix(editor_client, make_page, druga):
    _page, content = _draft(make_page, druga.site, "Druga")
    placeholder = content.rescan_placeholders()["body"]
    response = editor_client.get(
        "/druga/djcms/admin/cms/placeholder/add-plugin/",
        {
            "placeholder_id": placeholder.pk,
            "plugin_type": "TextPlugin",
            "plugin_language": "pl",
            "plugin_position": 1,
        },
        HTTP_HOST="testserver",
    )
    assert response.status_code == 200
