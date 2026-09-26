"""Redaktor edytuje tylko swój konkurs (DJ-02 D5, S12).

Świat: konkurs ``kwantowa`` (host ``testserver``) i ``fizyka`` (``fizyka.example``), w każdym strona
robocza, przekierowanie i folder filera z plikiem. Redaktor A loguje się przez SSO z grupą
``redakcja:kwantowa``. Każdy test pyta o jedno miejsce panelu: swoje działa, cudze – odmowa, a stan
cudzego obiektu się nie zmienia.
"""

from __future__ import annotations

import pytest
from cms.toolbar.utils import get_object_edit_url
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.sites import permissions
from apps.sites.models import CompetitionSite

from .conftest import grant

pytestmark = pytest.mark.django_db

TREE = "/djcms/admin/cms/pagecontent/"


class World:
    pass


@pytest.fixture
def world(make_competition, make_page, competition_site, settings, tmp_path):
    from cms.models import PageContent
    from filer.models import File, Folder

    from apps.seo.models import Redirect

    settings.MEDIA_ROOT = str(tmp_path)
    w = World()
    w.a = competition_site
    w.b = make_competition("fizyka", hosts=["fizyka.example"], public_origin="https://fizyka.example")
    call_command("setup_djcms_groups", stdout=None)
    w.page_a = make_page("Strona-Alfa", "alfa", publish=False)
    w.page_b = make_page("Strona-Beta", "beta", publish=False, site=w.b.site)
    w.content_a = PageContent.admin_manager.get(page=w.page_a, language="pl")
    w.content_b = PageContent.admin_manager.get(page=w.page_b, language="pl")
    w.redirect_a = Redirect.objects.create(site=w.a.site, old_path="/stara-alfa/", new_path="/alfa/")
    w.redirect_b = Redirect.objects.create(site=w.b.site, old_path="/stara-beta/", new_path="/beta/")
    from apps.importer.services import competition_folder

    w.folder_a = competition_folder(w.a)
    w.folder_b = competition_folder(w.b)
    w.shared = Folder.objects.get(name=permissions.SHARED_FOLDER_NAME, parent__isnull=True)
    w.file_b = File.objects.create(original_filename="beta.txt", name="Plik-Beta", folder=w.folder_b)
    return w


@pytest.fixture
def as_a(world, sso_editor):
    return sso_editor(grant("kwantowa"))


def user():
    return get_user_model().objects.get(username="web:7")


# --- drzewo stron i przełącznik witryn ------------------------------------------------------------


def test_editor_sees_own_tree_only(world, as_a):
    tree = as_a.get(f"{TREE}get-tree/").content.decode()
    assert "Strona-Alfa" in tree
    assert "Strona-Beta" not in tree


def test_site_switch_to_a_foreign_site_is_refused(world, as_a):
    assert as_a.get(TREE, {"site": world.b.site_id}).status_code == 403
    assert as_a.get(f"{TREE}get-tree/", {"site": world.b.site_id}).status_code == 403
    assert as_a.get(TREE, {"site": world.a.site_id}).status_code == 200


def test_foreign_host_admin_is_refused(world, as_a):
    """Ta sama sesja pod hostem innego konkursu (w praktyce: prefiks pod ``SITE_DOMAIN``) – 403."""
    assert as_a.get(TREE, HTTP_HOST="fizyka.example").status_code == 403


def test_logout_stays_reachable_from_a_foreign_site(world, as_a):
    response = as_a.post("/djcms/admin/logout/", HTTP_HOST="fizyka.example")
    assert response.status_code == 200
    assert "_auth_user_id" not in as_a.session


# --- strony, wtyczki, wersje -----------------------------------------------------------------------


def test_page_permissions_follow_the_site(world, as_a):
    from cms.utils.page_permissions import user_can_change_page, user_can_publish_page

    editor = user()
    assert user_can_change_page(editor, world.page_a)
    assert user_can_publish_page(editor, world.page_a)
    assert not user_can_change_page(editor, world.page_b)
    assert not user_can_publish_page(editor, world.page_b)


def test_foreign_page_edit_endpoint_is_refused(world, as_a):
    assert as_a.get(get_object_edit_url(world.content_a)).status_code == 200
    assert as_a.get(get_object_edit_url(world.content_b)).status_code in (403, 404)


def test_foreign_placeholder_does_not_accept_plugins(world, as_a):
    placeholder = world.content_b.rescan_placeholders()["body"]
    response = as_a.get(
        "/djcms/admin/cms/placeholder/add-plugin/",
        {
            "placeholder_id": placeholder.pk,
            "plugin_type": "TextPlugin",
            "plugin_language": "pl",
            "plugin_position": 1,
        },
    )
    assert response.status_code == 403
    assert not placeholder.get_plugins().exists()


def test_foreign_version_cannot_be_published(world, as_a):
    from djangocms_versioning.models import Version

    own = Version.objects.get_for_content(world.content_a)
    foreign = Version.objects.get_for_content(world.content_b)
    url = "/djcms/admin/djangocms_versioning/pagecontentversion/{}/publish/"

    as_a.post(url.format(foreign.pk))
    assert Version.objects.get(pk=foreign.pk).state == "draft"
    assert as_a.post(url.format(own.pk)).status_code == 302
    assert Version.objects.get(pk=own.pk).state == "published"


def test_draft_only_editor_cannot_publish(world, sso_editor):
    from cms.utils.page_permissions import user_can_change_page, user_can_publish_page

    sso_editor(grant("kwantowa", "edit"))
    assert user_can_change_page(user(), world.page_a)
    assert not user_can_publish_page(user(), world.page_a)


# --- bez prawa publikacji: opublikowane adresy stoją (apps.sites.live_guard) --------------------------

DRAFT_ONLY = ("kwantowa", "edit")


def page_path(page) -> str:
    from cms.models import PageUrl

    return PageUrl.objects.get(page=page, language="pl").path


def delete_page(client, page):
    return client.post(f"/djcms/admin/cms/page/{page.pk}/delete/", {"post": "yes"})


def move_page(client, page, target) -> int:
    """Status przeniesienia – widok django CMS owija każdą odpowiedź w JSON 200 (``jsonify_request``)."""
    response = client.post(
        f"/djcms/admin/cms/page/{page.pk}/move-page/", {"target": target.pk, "position": 0}
    )
    assert response.status_code == 200
    return response.json()["status"]


def page_exists(page) -> bool:
    from cms.models import Page

    return Page.objects.filter(pk=page.pk).exists()


def test_guard_is_installed_where_the_admin_and_toolbar_look():
    from cms import cms_toolbars
    from cms.utils import page_permissions

    from apps.sites import live_guard

    assert live_guard.is_installed()
    assert cms_toolbars.user_can_delete_page is page_permissions.user_can_delete_page


def test_draft_only_editor_cannot_delete_a_live_page(world, sso_editor, make_page):
    live = make_page("Strona-Zywa", "zywa")
    client = sso_editor(grant(*DRAFT_ONLY))

    assert not live.has_delete_permission(user())
    assert delete_page(client, live).status_code == 403
    assert page_exists(live)


def test_draft_only_editor_cannot_delete_a_draft_above_a_live_subpage(world, sso_editor, make_page):
    make_page("Podstrona-Zywa", "pod", parent=world.page_a)
    client = sso_editor(grant(*DRAFT_ONLY))

    assert delete_page(client, world.page_a).status_code == 403
    assert page_exists(world.page_a)


def test_draft_only_editor_deletes_a_page_that_was_never_published(world, sso_editor):
    """Jak w Wagtailu (``Editors``): własny szkic redaktor bez publikacji sprząta sam."""
    client = sso_editor(grant(*DRAFT_ONLY))

    assert delete_page(client, world.page_a).status_code == 302
    assert not page_exists(world.page_a)


def test_publisher_deletes_a_live_page(world, sso_editor, make_page):
    live = make_page("Strona-Zywa", "zywa")
    client = sso_editor(grant("kwantowa"))

    assert delete_page(client, live).status_code == 302
    assert not page_exists(live)


def test_draft_only_editor_cannot_move_a_live_page(world, sso_editor, make_page):
    live = make_page("Strona-Zywa", "zywa")
    client = sso_editor(grant(*DRAFT_ONLY))

    assert not live.has_move_page_permission(user())
    assert move_page(client, live, world.page_a) == 403
    assert page_path(live) == "zywa"


def test_draft_only_editor_moves_a_page_that_was_never_published(world, sso_editor, make_page):
    target = make_page("Cel", "cel")
    client = sso_editor(grant(*DRAFT_ONLY))

    assert move_page(client, world.page_a, target) == 200
    world.page_a.refresh_from_db()
    assert world.page_a.parent == target


def test_publisher_moves_a_live_page(world, sso_editor, make_page):
    live = make_page("Strona-Zywa", "zywa")
    target = make_page("Cel", "cel")
    client = sso_editor(grant("kwantowa"))

    assert move_page(client, live, target) == 200
    assert page_path(live) == "cel/zywa"


def test_page_settings_do_not_change_a_live_address(world, sso_editor, make_page, superuser):
    """Slug w ustawieniach: opublikowana treść jest tylko do odczytu (także dla publikującego),
    a zmiana w szkicu zmienia adres dopiero po publikacji – czyli rękami kogoś z prawem publikacji.
    """
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    live = make_page("Strona-Zywa", "zywa")
    published = PageContent.admin_manager.get(page=live, language="pl")
    client = sso_editor(grant(*DRAFT_ONLY))
    form = {"title": "Strona-Zywa", "slug": "nowy-adres", "template": "dj/pages/content.html"}
    url = "/djcms/admin/cms/pagecontent/{}/change/"

    assert client.post(url.format(published.pk), form).status_code == 403
    assert page_path(live) == "zywa"

    draft = Version.objects.get_for_content(published).copy(user()).content
    response = client.post(url.format(draft.pk), form)
    assert response.status_code == 302, response.context["adminform"].form.errors
    assert page_path(live) == "zywa"
    Version.objects.get_for_content(draft).publish(superuser)
    assert page_path(live) == "nowy-adres"


def test_draft_only_editor_cannot_change_the_home_page(world, sso_editor, make_page):
    home = make_page("Start", "start", home=True)
    client = sso_editor(grant(*DRAFT_ONLY))

    assert client.post(f"/djcms/admin/cms/page/{world.page_a.pk}/set-home/").status_code == 403
    home.refresh_from_db()
    assert home.is_home


def test_publisher_changes_the_home_page(world, sso_editor, make_page):
    home = make_page("Start", "start", home=True)
    client = sso_editor(grant("kwantowa"))

    assert client.post(f"/djcms/admin/cms/page/{world.page_a.pk}/set-home/").status_code in (200, 302)
    home.refresh_from_db()
    assert not home.is_home


# --- przekierowania i filer -------------------------------------------------------------------------


def test_foreign_redirect_is_invisible(world, as_a):
    listing = as_a.get("/djcms/admin/dj_seo/redirect/").content.decode()
    assert "/stara-alfa/" in listing
    assert "/stara-beta/" not in listing
    assert as_a.get(f"/djcms/admin/dj_seo/redirect/{world.redirect_b.pk}/change/").status_code in (302, 404)


def test_filer_folders_are_per_competition(world, as_a):
    listing = "/djcms/admin/filer/folder/{}/list/"
    assert as_a.get(listing.format(world.folder_a.pk)).status_code == 200
    assert as_a.get(listing.format(world.folder_b.pk)).status_code in (403, 404)
    root = as_a.get("/djcms/admin/filer/folder/").content.decode()
    assert world.folder_a.name in root
    assert world.folder_b.name not in root


def test_foreign_file_cannot_be_changed_or_deleted(world, as_a):
    from filer.models import File

    for url in (
        f"/djcms/admin/filer/file/{world.file_b.pk}/change/",
        f"/djcms/admin/filer/file/{world.file_b.pk}/delete/",
    ):
        response = as_a.post(url, {"name": "Podmieniony", "post": "yes"})
        assert response.status_code in (302, 403, 404), url
    assert File.objects.get(pk=world.file_b.pk).name == "Plik-Beta"


def test_shared_folder_is_read_only_for_editors(world, as_a):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = user()
    assert world.shared.has_read_permission(request)
    assert not world.shared.has_edit_permission(request)
    assert world.folder_a.has_edit_permission(request)
    assert not world.folder_b.has_read_permission(request)


def test_foreign_clipboard_and_thumbnail_presets_are_closed(world, as_a, superuser):
    """Cudzy schowek filera (nazwy plików innego konkursu) i wspólne presety miniatur – poza redakcją."""
    from filer.models import Clipboard, ClipboardItem, ThumbnailOption

    clipboard = Clipboard.objects.create(user=superuser)
    ClipboardItem.objects.create(clipboard=clipboard, file=world.file_b)
    preset = ThumbnailOption.objects.create(name="Kafelek", width=100, height=100)

    response = as_a.get(f"/djcms/admin/filer/clipboard/{clipboard.pk}/change/")
    assert response.status_code in (302, 403, 404)
    assert "Plik-Beta" not in response.content.decode()
    as_a.post(f"/djcms/admin/filer/thumbnailoption/{preset.pk}/change/", {"name": "Podmieniony"})
    assert ThumbnailOption.objects.get(pk=preset.pk).name == "Kafelek"
    editor = user()
    for codename in ("change_clipboard", "view_clipboarditem", "change_thumbnailoption"):
        assert not editor.has_perm(f"filer.{codename}"), codename


def test_editor_uploads_into_own_folder(world, as_a):
    """Bez schowka wgrywanie działa: ``filer.add_file`` + prawo do folderu konkursu."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from filer.models import File

    upload = SimpleUploadedFile("nowy.txt", b"tresc", content_type="text/plain")
    response = as_a.post(
        f"/djcms/admin/filer/clipboard/operations/upload/{world.folder_a.pk}/", {"file": upload}
    )
    assert response.status_code == 200, response.content[:300]
    assert File.objects.filter(folder=world.folder_a, original_filename="nowy.txt").exists()


# --- platforma i zasięg ----------------------------------------------------------------------------


def test_platform_editor_reaches_every_site(world, sso_editor):
    client = sso_editor(platform=True)
    assert client.get(TREE, {"site": world.b.site_id}).status_code == 200
    assert client.get(TREE, HTTP_HOST="fizyka.example").status_code == 200
    assert permissions.editable_site_ids(user()) is None


def test_editable_site_ids_follow_the_groups(world, sso_editor):
    sso_editor(grant("kwantowa"), grant("fizyka", "edit"))
    assert permissions.editable_site_ids(user()) == {world.a.site_id, world.b.site_id}


def test_superuser_is_not_scoped(world, client, superuser):
    client.force_login(superuser)
    assert client.get(TREE, {"site": world.b.site_id}).status_code == 200


# --- zakładanie grup i folderów ----------------------------------------------------------------------


def test_groups_hold_site_scoped_global_permissions_only(world):
    from cms.models import GlobalPagePermission, PagePermission

    for slug, site, publish in (("kwantowa", world.a.site, True), ("fizyka", world.b.site, True)):
        for draft in (False, True):
            name = permissions.group_name(slug, publish=not draft)
            (row,) = GlobalPagePermission.objects.filter(group__name=name)
            assert list(row.sites.all()) == [site]
            assert row.can_publish is (publish and not draft)
            assert not row.can_change_permissions and not row.can_change_advanced_settings
    (platform,) = GlobalPagePermission.objects.filter(group__name=permissions.PLATFORM_GROUP)
    assert not platform.sites.exists()
    assert not PagePermission.objects.exists()


def test_registry_sync_creates_groups_and_folder_for_a_new_competition():
    from django.contrib.auth.models import Group
    from filer.models import Folder, FolderPermission

    from apps.sites.registry import sync_registry

    from .test_registry import entry

    sync_registry({"competitions": [entry("chemia", name="Olimpiada Chemiczna")]})

    assert Group.objects.filter(name__in=["redakcja:chemia", "redakcja:chemia:bez-publikacji"]).count() == 2
    folder = Folder.objects.get(name="Konkurs: Olimpiada Chemiczna (chemia)", parent__isnull=True)
    assert FolderPermission.objects.filter(folder=folder, group__name="redakcja:chemia", can_edit=1).exists()
    site = CompetitionSite.objects.get(slug="chemia").site
    assert Group.objects.get(name="redakcja:chemia").globalpagepermission_set.get().sites.get() == site
