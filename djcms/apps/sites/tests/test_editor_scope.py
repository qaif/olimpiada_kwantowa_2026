"""Zasięg redaktora poza drzewem stron: rozszerzenia, schowek, wyszukiwarki panelu, filer (DJ-02g, S12).

Ten sam świat co w ``test_permissions`` – redaktor konkursu ``kwantowa`` (witryna A) i treść konkursu
``fizyka`` (witryna B). Każdy test sprawdza jedną drogę, którą redaktor A mógłby odczytać albo
zmienić coś z witryny B (albo cudzy schowek) – i że ta sama droga dla własnych obiektów działa.
"""

# Fikstury world/as_a importujemy z test_permissions (ten sam świat A/B), więc parametry
# testów o tych nazwach ruff bierze za przesłonięcie importu.
# ruff: noqa: F811

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from .test_permissions import TREE, as_a, user, world  # noqa: F401 – fikstury świata A/B

pytestmark = pytest.mark.django_db

ADMIN = "/djcms/admin"
PLACEHOLDER = f"{ADMIN}/cms/placeholder"
CMS_PATH = {"cms_path": "/alfa/"}


def body(content):
    return content.rescan_placeholders()["body"]


def text(placeholder, value: str):
    from cms.api import add_plugin

    return add_plugin(placeholder, "TextPlugin", "pl", body=f"<p>{value}</p>")


def own_clipboard(client, world):
    """Schowek redaktora A – zakłada go pasek narzędzi przy pierwszym żądaniu w trybie edycji."""
    from cms.models import UserSettings
    from cms.toolbar.utils import get_object_edit_url

    assert client.get(get_object_edit_url(world.content_a)).status_code == 200
    return UserSettings.objects.get(user=user()).clipboard


@pytest.fixture
def foreign_clipboard(world):
    """Schowek innego redaktora: skopiowany cały placeholder (``PlaceholderReference``) z tekstem."""
    from cms.models import Placeholder, UserSettings
    from cms.models.placeholderpluginmodel import PlaceholderReference

    other = get_user_model().objects.create(username="inny@example.com", is_staff=True)
    clipboard = Placeholder.objects.create(slot="clipboard")
    settings_ = UserSettings.objects.create(user=other, language="pl", clipboard=clipboard)
    clipboard.source = settings_
    clipboard.save()
    reference = PlaceholderReference.objects.create(
        name="Schowek innego", plugin_type="PlaceholderPlugin", language="pl", placeholder=clipboard
    )
    inner = text(reference.placeholder_ref, "Tajny-Schowek")
    return clipboard, reference, inner


def texts(placeholder) -> list[str]:
    return [plugin.get_bound_plugin().body for plugin in placeholder.get_plugins("pl")]


# --- 1. rozszerzenia stron: lista, akcje zbiorcze, usuwanie ---------------------------------------


@pytest.fixture
def metas(world):
    from apps.pages.models import MenuExtension, NewsMeta

    return {
        "news_a": NewsMeta.objects.create(extended_object=world.content_a, lead="Lead-Alfa"),
        "news_b": NewsMeta.objects.create(extended_object=world.content_b, lead="Lead-Beta"),
        "menu_a": MenuExtension.objects.create(extended_object=world.page_a),
        "menu_b": MenuExtension.objects.create(extended_object=world.page_b),
    }


@pytest.mark.parametrize(
    ("model", "own", "foreign"), [("newsmeta", "news_a", "news_b"), ("menuextension", "menu_a", "menu_b")]
)
def test_extension_changelist_lists_own_site_only(world, as_a, metas, model, own, foreign):
    response = as_a.get(f"{ADMIN}/dj_pages/{model}/")
    assert response.status_code == 200
    listed = set(response.context["cl"].queryset.values_list("pk", flat=True))
    assert listed == {metas[own].pk}


@pytest.mark.parametrize("select_across", ["0", "1"])
def test_bulk_delete_of_extensions_is_off(world, as_a, metas, select_across):
    from apps.pages.models import NewsMeta

    as_a.post(
        f"{ADMIN}/dj_pages/newsmeta/",
        {
            "action": "delete_selected",
            "_selected_action": [metas["news_a"].pk, metas["news_b"].pk],
            "select_across": select_across,
            "index": "0",
            "post": "yes",
        },
    )
    assert NewsMeta.objects.count() == 2


def test_extension_admin_has_no_actions(world, as_a):
    from django.contrib import admin
    from django.test import RequestFactory

    from apps.pages.models import MenuExtension, NewsMeta

    request = RequestFactory().get("/")
    request.user = user()
    for model in (NewsMeta, MenuExtension):
        assert admin.site.get_model_admin(model).get_actions(request) == {}


def test_metadata_of_a_published_version_cannot_be_deleted(world, as_a, metas):
    from djangocms_versioning.models import Version

    from apps.pages.models import NewsMeta

    Version.objects.get_for_content(world.content_a).publish(user())
    url = f"{ADMIN}/dj_pages/newsmeta/{metas['news_a'].pk}/delete/"
    assert as_a.post(url, {"post": "yes"}).status_code == 403
    assert NewsMeta.objects.filter(pk=metas["news_a"].pk).exists()


def test_metadata_of_a_draft_can_be_deleted(world, as_a, metas):
    from apps.pages.models import NewsMeta

    url = f"{ADMIN}/dj_pages/newsmeta/{metas['news_a'].pk}/delete/"
    assert as_a.post(url, {"post": "yes"}).status_code == 302
    assert not NewsMeta.objects.filter(pk=metas["news_a"].pk).exists()


def test_foreign_metadata_cannot_be_deleted(world, as_a, metas):
    from apps.pages.models import NewsMeta

    url = f"{ADMIN}/dj_pages/newsmeta/{metas['news_b'].pk}/delete/"
    assert as_a.post(url, {"post": "yes"}).status_code in (403, 404)
    assert NewsMeta.objects.filter(pk=metas["news_b"].pk).exists()


# --- 2. schowek: cudzy – ani do odczytu, ani do zmiany; własny działa ------------------------------


def test_own_copy_and_paste_still_works(world, as_a):
    from cms.models import CMSPlugin

    target = body(world.content_a)
    source = text(target, "Tekst-Alfa")
    clipboard = own_clipboard(as_a, world)

    # Kopia jednej wtyczki do schowka i wklejenie jej z powrotem („wklej” = move-plugin z kopią).
    copied = as_a.post(
        f"{PLACEHOLDER}/copy-plugins/?cms_path=/alfa/",
        {
            "source_placeholder_id": target.pk,
            "source_plugin_id": source.pk,
            "source_language": "pl",
            "target_placeholder_id": clipboard.pk,
            "target_language": "pl",
        },
    )
    assert copied.status_code == 200, copied.content[:300]
    in_clipboard = CMSPlugin.objects.get(placeholder=clipboard)
    pasted = as_a.post(
        f"{PLACEHOLDER}/move-plugin/?cms_path=/alfa/",
        {
            "plugin_id": in_clipboard.pk,
            "placeholder_id": target.pk,
            "target_language": "pl",
            "target_position": 2,
            "move_a_copy": "true",
        },
    )
    assert pasted.status_code == 200, pasted.content[:300]
    assert texts(target) == ["<p>Tekst-Alfa</p>", "<p>Tekst-Alfa</p>"]

    # Kopia całego placeholdera (``PlaceholderReference``) i wklejenie go do tego samego miejsca.
    copied = as_a.post(
        f"{PLACEHOLDER}/copy-plugins/?cms_path=/alfa/",
        {
            "source_placeholder_id": target.pk,
            "source_language": "pl",
            "target_placeholder_id": clipboard.pk,
            "target_language": "pl",
        },
    )
    assert copied.status_code == 200, copied.content[:300]
    reference = CMSPlugin.objects.get(placeholder=clipboard)
    assert reference.plugin_type == "PlaceholderPlugin"
    pasted = as_a.post(
        f"{PLACEHOLDER}/move-plugin/?cms_path=/alfa/",
        {
            "plugin_id": reference.pk,
            "placeholder_id": target.pk,
            "target_language": "pl",
            "target_position": 3,
            "move_a_copy": "true",
        },
    )
    assert pasted.status_code == 200, pasted.content[:300]
    assert len(texts(target)) == 4


@pytest.mark.parametrize("into", ["page", "clipboard"])
def test_foreign_clipboard_cannot_be_copied(world, as_a, foreign_clipboard, into):
    _clipboard, reference, _inner = foreign_clipboard
    target = body(world.content_a) if into == "page" else own_clipboard(as_a, world)
    response = as_a.post(
        f"{PLACEHOLDER}/copy-plugins/?cms_path=/alfa/",
        {
            "source_placeholder_id": reference.placeholder_ref.pk,
            "source_language": "pl",
            "target_placeholder_id": target.pk,
            "target_language": "pl",
        },
    )
    assert response.status_code == 403
    assert "Tajny-Schowek" not in "".join(texts(target))
    assert b"Tajny-Schowek" not in response.content


def test_foreign_clipboard_plugin_cannot_be_pasted(world, as_a, foreign_clipboard):
    _clipboard, _reference, inner = foreign_clipboard
    target = body(world.content_a)
    response = as_a.post(
        f"{PLACEHOLDER}/move-plugin/?cms_path=/alfa/",
        {
            "plugin_id": inner.pk,
            "placeholder_id": target.pk,
            "target_language": "pl",
            "target_position": 1,
            "move_a_copy": "true",
        },
    )
    assert response.status_code == 403
    assert not texts(target)


def test_foreign_clipboard_plugin_cannot_be_moved_out(world, as_a, foreign_clipboard):
    from cms.models import CMSPlugin

    _clipboard, reference, inner = foreign_clipboard
    response = as_a.post(
        f"{PLACEHOLDER}/move-plugin/?cms_path=/alfa/",
        {"plugin_id": inner.pk, "placeholder_id": body(world.content_a).pk, "target_language": "pl"},
    )
    assert response.status_code == 403
    assert CMSPlugin.objects.get(pk=inner.pk).placeholder_id == reference.placeholder_ref.pk


def test_foreign_clipboard_plugin_cannot_be_opened_edited_or_deleted(world, as_a, foreign_clipboard):
    from cms.models import CMSPlugin

    _clipboard, reference, inner = foreign_clipboard
    for url in (
        f"{PLACEHOLDER}/edit-plugin/{inner.pk}/?cms_path=/alfa/",
        f"{PLACEHOLDER}/edit-field/{inner.pk}/pl/?edit_fields=body",
        f"{PLACEHOLDER}/delete-plugin/{inner.pk}/?cms_path=/alfa/",
        f"{PLACEHOLDER}/edit-plugin/{reference.pk}/?cms_path=/alfa/",
        f"{PLACEHOLDER}/clear-placeholder/{reference.placeholder_ref.pk}/?cms_path=/alfa/",
    ):
        assert as_a.get(url).status_code == 403, url
        assert as_a.post(url, {"post": "yes", "body": "<p>Podmiana</p>"}).status_code == 403, url
    assert CMSPlugin.objects.get(pk=inner.pk).get_bound_plugin().body == "<p>Tajny-Schowek</p>"


def test_foreign_site_plugin_cannot_be_copied(world, as_a):
    source = body(world.content_b)
    text(source, "Tekst-Beta")
    target = body(world.content_a)
    response = as_a.post(
        f"{PLACEHOLDER}/copy-plugins/?cms_path=/alfa/",
        {
            "source_placeholder_id": source.pk,
            "source_language": "pl",
            "target_placeholder_id": target.pk,
            "target_language": "pl",
        },
    )
    assert response.status_code == 403
    assert not texts(target)


def test_own_clipboard_reference_is_not_rendered_nor_edited_in_place(world, as_a):
    """Wnętrze ``PlaceholderReference`` nie ma własnego ekranu – upstream odpowiada 500, tu 403/404."""
    from cms.models import CMSPlugin
    from django.contrib.contenttypes.models import ContentType

    target = body(world.content_a)
    text(target, "Tekst-Alfa")
    clipboard = own_clipboard(as_a, world)
    as_a.post(
        f"{PLACEHOLDER}/copy-plugins/?cms_path=/alfa/",
        {
            "source_placeholder_id": target.pk,
            "source_language": "pl",
            "target_placeholder_id": clipboard.pk,
            "target_language": "pl",
        },
    )
    reference = CMSPlugin.objects.get(placeholder=clipboard).get_bound_plugin()
    inner = reference.placeholder_ref.get_plugins("pl").get()
    content_type = ContentType.objects.get_for_model(reference)
    for url in (
        f"{PLACEHOLDER}/edit-plugin/{inner.pk}/?cms_path=/alfa/",
        f"{PLACEHOLDER}/delete-plugin/{inner.pk}/?cms_path=/alfa/",
        f"{PLACEHOLDER}/object/{content_type.pk}/structure/{reference.pk}/",
        f"{PLACEHOLDER}/object/{content_type.pk}/preview/{reference.pk}/",
        f"{PLACEHOLDER}/object/{content_type.pk}/edit/{reference.pk}/",
    ):
        assert as_a.get(url).status_code in (403, 404), url


# --- 3. wybór linku w edytorze tekstu (djangocms-text) ---------------------------------------------

LINKS = f"{ADMIN}/cms/page/plugin/text_plugin/urls/"


def test_link_search_lists_own_pages_only(world, as_a):
    response = as_a.get(LINKS, {"q": "strona"})
    assert response.status_code == 200
    found = [child["id"] for group in response.json()["results"] for child in group["children"]]
    assert found == [f"cms.page:{world.page_a.pk}"]


def test_link_lookup_of_a_foreign_page_is_refused(world, as_a):
    assert as_a.get(LINKS, {"g": f"cms.page:{world.page_a.pk}"}).json()["text"] == "Strona-Alfa"
    response = as_a.get(LINKS, {"g": f"cms.page:{world.page_b.pk}"})
    assert response.status_code == 403
    assert b"Strona-Beta" not in response.content


# --- 4. autocomplete panelu ----------------------------------------------------------------------

AUTOCOMPLETE = f"{ADMIN}/autocomplete/"


def test_version_grouper_autocomplete_lists_own_pages_only(world, as_a):
    """Formularz wyboru strony listy wersji (djangocms-versioning ``…/select/``) – działa, ale tylko A."""
    grouper = as_a.get(f"{ADMIN}/djangocms_versioning/pagecontentversion/select/")
    assert grouper.status_code == 200
    assert "autocomplete" in grouper.content.decode()
    response = as_a.get(
        AUTOCOMPLETE, {"app_label": "cms", "model_name": "pagecontent", "field_name": "page", "term": ""}
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [str(world.page_a.pk)]


@pytest.mark.parametrize(
    "triple",
    [
        ("filer", "file", "folder"),
        ("filer", "folder", "parent"),
        ("filer", "clipboarditem", "file"),
        ("dj_blocks", "attachment", "file"),
        ("dj_pages", "menuextension", "extended_object"),
    ],
)
def test_other_autocompletes_are_refused(world, as_a, triple):
    app_label, model_name, field_name = triple
    response = as_a.get(
        AUTOCOMPLETE, {"app_label": app_label, "model_name": model_name, "field_name": field_name, "term": ""}
    )
    assert response.status_code == 403
    assert b"Konkurs" not in response.content and b"beta" not in response.content.lower()


def test_superuser_autocomplete_is_untouched(world, client, superuser):
    client.force_login(superuser)
    response = client.get(
        AUTOCOMPLETE, {"app_label": "filer", "model_name": "file", "field_name": "folder", "term": ""}
    )
    assert response.status_code == 200


# --- 5. filer: listy plików i wyszukiwanie ---------------------------------------------------------


@pytest.mark.parametrize("model", ["file", "image"])
def test_filer_file_changelists_are_refused(world, as_a, model):
    response = as_a.get(f"{ADMIN}/filer/{model}/")
    assert response.status_code == 403
    assert b"Plik-Beta" not in response.content


def test_filer_search_skips_foreign_unfiled_files(world, as_a):
    from filer.models import File

    other = get_user_model().objects.create(username="inny@example.com", is_staff=True)
    File.objects.create(original_filename="luzem-cudzy.txt", name="Luzem-Cudzy", owner=other)
    File.objects.create(original_filename="luzem-moj.txt", name="Luzem-Moj", owner=user())
    File.objects.create(original_filename="alfa.txt", name="Luzem-Alfa", folder=world.folder_a)
    listing = as_a.get(f"{ADMIN}/filer/folder/", {"q": "Luzem"}).content.decode()
    assert "Luzem-Moj" in listing
    assert "Luzem-Alfa" in listing
    assert "Luzem-Cudzy" not in listing
    assert as_a.get(f"{ADMIN}/filer/folder/images_with_missing_data/").status_code == 403


# --- 6. przełącznik witryn w formularzu wieloczęściowym i w zapisie „2.0” ---------------------------


def site_allowed(world, method: str, data, **extra) -> bool:
    """``_site_allowed`` wprost: odmowę widoku django CMS (cudze drzewo) trzeba odróżnić od naszej."""
    from django.test import RequestFactory

    from apps.sites.middleware import EditorAccessMiddleware

    request = getattr(RequestFactory(), method)("/djcms/admin/cms/pagecontent/", data, **extra)
    request.user = user()
    request.site = world.a.site
    return EditorAccessMiddleware._site_allowed(request)


def test_site_switch_in_a_multipart_post_is_refused(world, as_a):
    # ``RequestFactory.post(data=dict)`` wysyła multipart/form-data – jak formularz z plikiem.
    assert not site_allowed(world, "post", {"site": world.b.site_id})
    assert site_allowed(world, "post", {"site": world.a.site_id})
    assert not site_allowed(
        world, "post", f"site={world.b.site_id}", content_type="application/x-www-form-urlencoded"
    )


@pytest.mark.parametrize("value", ["{}.0", " {} ", "{}.00"])
def test_site_switch_parsed_like_django_cms(world, as_a, value):
    from cms.utils.admin import get_site_from_request
    from django.test import RequestFactory

    request = RequestFactory().get("/", {"site": value.format(world.b.site_id)})
    request.site = world.a.site
    assert get_site_from_request(request) == world.b.site  # django CMS czyta to jako witrynę B
    assert not site_allowed(world, "get", {"site": value.format(world.b.site_id)})
    assert site_allowed(world, "get", {"site": value.format(world.a.site_id)})


def test_unparseable_site_is_refused(world, as_a):
    assert not site_allowed(world, "get", {"site": "abc"})
    assert site_allowed(world, "get", {"site": ""})
    assert as_a.get(TREE, {"site": "abc"}).status_code == 403
    assert as_a.get(TREE, {"site": str(world.a.site_id)}).status_code == 200


# --- 7. nowa strona z cudzej: kopia, tłumaczenie, rodzic ------------------------------------------


def test_duplicate_with_a_foreign_source_is_refused(world, as_a):
    from cms.models import Page

    before = Page.objects.count()
    response = as_a.post(
        f"{ADMIN}/cms/pagecontent/{world.content_a.pk}/duplicate/?language=pl",
        {"source": world.page_b.pk, "title": "Kopia", "slug": "kopia"},
    )
    assert response.status_code == 403
    assert Page.objects.count() == before


@pytest.mark.parametrize("param", ["source", "cms_page", "parent_page"])
def test_add_page_form_with_a_foreign_page_is_refused(world, as_a, param):
    add = f"{ADMIN}/cms/pagecontent/add/"
    assert as_a.get(add, {param: world.page_b.pk, "language": "pl"}).status_code == 403
    assert as_a.get(add, {param: world.page_a.pk, "language": "pl"}).status_code in (200, 302)


# --- 8. „zobacz na stronie” (``admin/r/<typ>/<id>/``) ---------------------------------------------


def test_view_on_site_of_a_foreign_or_unpublished_page(world, as_a, make_page):
    from cms.models import Page
    from django.contrib.contenttypes.models import ContentType

    page_type = ContentType.objects.get_for_model(Page).pk
    assert as_a.get(f"{ADMIN}/r/{page_type}/{world.page_b.pk}/").status_code == 403
    assert as_a.get(f"{ADMIN}/r/{page_type}/{world.page_a.pk}/").status_code == 404
    live = make_page("Strona-Zywa", "zywa")
    assert as_a.get(f"{ADMIN}/r/{page_type}/{live.pk}/").status_code == 302


# --- zwykła praca redaktora we własnej witrynie – bez zmian ----------------------------------------


def test_editor_workflow_in_own_site_still_works(world, as_a):
    """Nowa strona, wtyczka tekstu (dodanie i edycja), metryka z paska i wgranie pliku strumieniem."""
    from cms.models import CMSPlugin, PageContent
    from filer.models import File

    from apps.pages.models import NewsMeta

    created = as_a.post(
        f"{ADMIN}/cms/pagecontent/add/?language=pl",
        {"title": "Nowa-Alfa", "slug": "nowa-alfa", "template": "dj/pages/content.html"},
    )
    assert created.status_code == 302, created.content[:300]
    content = PageContent.admin_manager.get(title="Nowa-Alfa")
    assert content.page.site_id == world.a.site_id

    placeholder = body(content)
    query = f"placeholder_id={placeholder.pk}&plugin_type=TextPlugin&plugin_language=pl&plugin_position=1"
    add_url = f"{PLACEHOLDER}/add-plugin/?{query}&cms_path=/nowa-alfa/"
    assert as_a.get(add_url).status_code == 200
    assert as_a.post(add_url, {"body": "<p>Pierwsza</p>"}).status_code == 200
    plugin = CMSPlugin.objects.get(placeholder=placeholder)
    edit_url = f"{PLACEHOLDER}/edit-plugin/{plugin.pk}/?cms_path=/nowa-alfa/"
    assert as_a.post(edit_url, {"body": "<p>Poprawiona</p>"}).status_code == 200
    assert texts(placeholder) == ["<p>Poprawiona</p>"]

    meta = as_a.post(
        f"{ADMIN}/dj_pages/newsmeta/add/?extended_object={content.pk}",
        {"date": "2026-09-01", "lead": "Lead-Nowej"},
    )
    assert meta.status_code == 302, meta.content[:300]
    assert NewsMeta.objects.get(extended_object=content).lead == "Lead-Nowej"

    # Wgranie „surowym” strumieniem (XHR bez multipart) – ``handle_upload`` filera czyta ciało żądania sam,
    # więc warstwa nie może go wcześniej skonsumować.
    raw = as_a.post(
        f"{ADMIN}/filer/clipboard/operations/upload/{world.folder_a.pk}/?qqfile=strumien.txt",
        b"tresc pliku",
        content_type="application/octet-stream",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert raw.status_code == 200, raw.content[:300]
    assert File.objects.filter(folder=world.folder_a, original_filename="strumien.txt").exists()
