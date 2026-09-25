"""``/cms/`` per konkurs: koordynator konkursu A nie widzi i nie zmienia niczego z konkursu B.

Świat testów: dwa konkursy z egzekwowanymi członkostwami, po jednym koordynatorze, w każdym
strona, obraz, dokument i komunikat z **rozpoznawalnym tytułem**, a potem przebieg
``scope_cms_access`` — czyli dokładnie droga produkcyjna. Każdy test pyta o jedno miejsce
``/cms/`` i sprawdza dwie rzeczy: swoje widać, cudzego tytułu **nie ma w odpowiedzi**. Tytuł, a nie
liczba wierszy, bo wyciekiem jest właśnie tytuł (okna wyboru, dziennik, wyszukiwarka).

Na końcu dwie kontrole odwrotne: superużytkownik i superkoordynator widzą oba konkursy.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image as PILImage
from wagtail.documents import get_document_model
from wagtail.images import get_image_model
from wagtail.log_actions import log
from wagtail.models import Page

from apps.accounts import super_coordinator
from apps.accounts.models import CompetitionRole
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import UserFactory
from apps.cms.models import ContentPage, HomePage
from apps.cms.permissions import cms_group_name, ensure_collection
from apps.cms.tests.factories import AnnouncementFactory

pytestmark = pytest.mark.django_db

TITLE_A = "Tajemnica-Alfa"
TITLE_B = "Tajemnica-Beta"


def png(name: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


class World:
    """Uchwyty do obu konkursów — po jednym obiekcie każdego rodzaju na konkurs."""


@pytest.fixture
def world(competition, other_competition, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    w = World()
    w.a, w.b = competition, other_competition
    for row in (w.a, w.b):
        row.feature_flags = {**(row.feature_flags or {}), "memberships_enforced": True}
        row.save(update_fields=["feature_flags"])

    w.coord_a = UserFactory(email="koordynator-a@example.test")
    grant_role(w.coord_a, CompetitionRole.COORDINATOR, competition=w.a)
    w.coord_b = UserFactory(email="koordynator-b@example.test")
    grant_role(w.coord_b, CompetitionRole.COORDINATOR, competition=w.b)

    home_a = HomePage.objects.descendant_of(w.a.site.root_page, inclusive=True).first()
    w.page_a = home_a.add_child(instance=ContentPage(title=f"{TITLE_A} strona", slug="tajna-a"))
    root_b = Page.objects.get(pk=w.b.site.root_page_id)
    w.page_b = root_b.add_child(instance=ContentPage(title=f"{TITLE_B} strona", slug="tajna-b"))

    Image, Document = get_image_model(), get_document_model()
    w.image_a = Image.objects.create(
        title=f"{TITLE_A} obraz", file=png("a.png"), collection=ensure_collection(w.a)
    )
    w.image_b = Image.objects.create(
        title=f"{TITLE_B} obraz", file=png("b.png"), collection=ensure_collection(w.b)
    )
    w.doc_a = Document.objects.create(
        title=f"{TITLE_A} dokument", file=pdf("a.pdf"), collection=ensure_collection(w.a)
    )
    w.doc_b = Document.objects.create(
        title=f"{TITLE_B} dokument", file=pdf("b.pdf"), collection=ensure_collection(w.b)
    )
    w.ann_a = AnnouncementFactory(competition=w.a, text=f"{TITLE_A} komunikat")
    w.ann_b = AnnouncementFactory(competition=w.b, text=f"{TITLE_B} komunikat")

    call_command("scope_cms_access", "--root-media-to", w.a.slug, stdout=io.StringIO())
    return w


@pytest.fixture
def as_a(world, client_for):
    client = client_for(world.a)
    client.force_login(world.coord_a)
    return client


def body(response) -> str:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content).decode()
    return response.content.decode()


def assert_scoped(response):
    """Swoje widać, cudzego tytułu nie ma — w jednej odpowiedzi."""
    assert response.status_code == 200, response.status_code
    text = body(response)
    assert TITLE_A in text
    assert TITLE_B not in text


# --- stan po komendzie ----------------------------------------------------------------------------


def test_each_coordinator_is_in_the_group_of_their_competition_only(world):
    assert set(world.coord_a.groups.filter(name__startswith="cms:").values_list("name", flat=True)) == {
        cms_group_name(world.a)
    }
    assert set(world.coord_b.groups.filter(name__startswith="cms:").values_list("name", flat=True)) == {
        cms_group_name(world.b)
    }


# --- strony ---------------------------------------------------------------------------------------


def test_foreign_page_cannot_be_edited(world, as_a):
    response = as_a.get(f"/cms/pages/{world.page_b.pk}/edit/")

    assert response.status_code in (302, 403)
    assert TITLE_B not in body(response)
    assert as_a.get(f"/cms/pages/{world.page_a.pk}/edit/").status_code == 200


def test_foreign_page_cannot_be_published_by_post(world, as_a):
    """IDOR po POST: zapis do cudzej strony nie przechodzi, a tytuł zostaje ten sam."""
    response = as_a.post(f"/cms/pages/{world.page_b.pk}/edit/", {"title": "Podmieniony", "slug": "tajna-b"})

    assert response.status_code in (302, 403)
    world.page_b.refresh_from_db()
    assert world.page_b.title == f"{TITLE_B} strona"


def test_foreign_page_cannot_be_deleted(world, as_a):
    response = as_a.post(f"/cms/pages/{world.page_b.pk}/delete/")

    assert response.status_code in (302, 403)
    assert Page.objects.filter(pk=world.page_b.pk).exists()


def test_explorer_of_the_foreign_tree_does_not_list_its_pages(world, as_a):
    response = as_a.get(f"/cms/pages/{world.b.site.root_page_id}/", follow=True)

    assert TITLE_B not in body(response)


def test_explorer_filters_do_not_name_the_foreign_site(world, as_a):
    """Filtr „Witryna” listy stron wymieniał nazwy **wszystkich** witryn instalacji."""
    from wagtail.models import Site

    Site.objects.filter(pk=world.b.site_id).update(site_name=f"{TITLE_B} witryna")
    Site.objects.filter(pk=world.a.site_id).update(site_name=f"{TITLE_A} witryna")

    assert_scoped(as_a.get(f"/cms/pages/{world.a.site.root_page_id}/"))


def test_page_search_lists_own_pages_only(world, as_a):
    assert_scoped(as_a.get("/cms/pages/search/", {"q": "Tajemnica"}))


# --- obrazy i dokumenty ---------------------------------------------------------------------------


def test_image_library_lists_own_collection_only(world, as_a):
    assert_scoped(as_a.get("/cms/images/"))


def test_document_library_lists_own_collection_only(world, as_a):
    assert_scoped(as_a.get("/cms/documents/"))


def test_foreign_image_and_document_cannot_be_edited_or_deleted(world, as_a):
    for url in (
        f"/cms/images/{world.image_b.pk}/",
        f"/cms/images/{world.image_b.pk}/delete/",
        f"/cms/documents/edit/{world.doc_b.pk}/",
        f"/cms/documents/delete/{world.doc_b.pk}/",
    ):
        response = as_a.get(url)
        assert response.status_code in (302, 403, 404), url
        assert TITLE_B not in body(response), url

    as_a.post(f"/cms/images/{world.image_b.pk}/delete/")
    as_a.post(f"/cms/documents/delete/{world.doc_b.pk}/")
    assert get_image_model().objects.filter(pk=world.image_b.pk).exists()
    assert get_document_model().objects.filter(pk=world.doc_b.pk).exists()


def test_upload_form_offers_own_collection_only(world, as_a):
    """Z jedną dostępną kolekcją Wagtail chowa pole wyboru — cudzej nazwy nie ma w żadnym razie."""
    response = as_a.get("/cms/images/multiple/add/")

    assert response.status_code == 200
    assert world.b.name not in body(response)


# --- okna wyboru ----------------------------------------------------------------------------------


def test_image_chooser_lists_own_collection_only(world, as_a):
    assert_scoped(as_a.get("/cms/images/chooser/"))


def test_document_chooser_lists_own_collection_only(world, as_a):
    assert_scoped(as_a.get("/cms/documents/chooser/"))


def test_foreign_image_cannot_be_chosen_by_id(world, as_a):
    response = as_a.get(f"/cms/images/chooser/{world.image_b.pk}/select_format/")

    assert response.status_code in (302, 403, 404)
    assert TITLE_B not in body(response)


def test_page_chooser_starts_in_own_tree(world, as_a):
    response = as_a.get("/cms/choose-page/", {"page_type": "wagtailcore.page"}, follow=True)

    assert_scoped(response)


def test_page_chooser_refuses_a_foreign_parent(world, as_a):
    assert as_a.get(f"/cms/choose-page/{world.b.site.root_page_id}/").status_code == 404
    assert as_a.get(f"/cms/choose-page/{world.page_b.pk}/").status_code == 404


def test_page_chooser_does_not_list_foreign_children_of_the_tree_root(world, as_a):
    """Korzeń drzewa jest drogą do własnego poddrzewa — ale jego lista dzieci nie zdradza cudzych."""
    tree_root = Page.get_first_root_node()
    response = as_a.get(f"/cms/choose-page/{tree_root.pk}/")

    assert response.status_code == 200
    assert world.b.site.root_page.title not in body(response)


def test_page_chooser_search_lists_own_pages_only(world, as_a):
    assert_scoped(as_a.get("/cms/choose-page/search/", {"q": "Tajemnica"}))


def test_snippet_chooser_lists_own_announcements_only(world, as_a):
    assert_scoped(as_a.get("/cms/snippets/choose/cms/announcement/"))
    assert as_a.get(f"/cms/snippets/choose/cms/announcement/chosen/{world.ann_b.pk}/").status_code == 404


def test_admin_api_lists_own_media_only(world, as_a):
    for url in ("/cms/api/main/images/", "/cms/api/main/documents/"):
        assert_scoped(as_a.get(url, {"fields": "title"}))
    assert as_a.get(f"/cms/api/main/images/{world.image_b.pk}/").status_code == 404


# --- komunikaty (snippet) i ustawienia ------------------------------------------------------------


def test_announcement_list_is_scoped(world, as_a):
    world.coord_a.user_permissions.add(*_announcement_permissions())
    assert_scoped(as_a.get("/cms/snippets/cms/announcement/"))


def test_foreign_announcement_is_404_for_every_view(world, as_a):
    """Zawężenie działa także wtedy, gdy ktoś **nada** redaktorowi prawa do komunikatów."""
    world.coord_a.user_permissions.add(*_announcement_permissions())
    base = "/cms/snippets/cms/announcement"
    for view in ("edit", "delete", "usage", "history"):
        assert as_a.get(f"{base}/{view}/{world.ann_b.pk}/").status_code == 404, view
    # Kopia sprawdza najpierw prawo „dodaj” pod adresem konkursu A — odmowa Wagtaila to
    # przekierowanie z komunikatem; ważne, że treść cudzego komunikatu nie wychodzi.
    copy = as_a.get(f"{base}/copy/{world.ann_b.pk}/", follow=True)
    assert TITLE_B not in body(copy)
    assert as_a.post(f"{base}/delete/{world.ann_b.pk}/").status_code == 404
    world.ann_b.refresh_from_db()
    assert as_a.get(f"{base}/edit/{world.ann_a.pk}/").status_code == 200


def test_announcement_cannot_be_added_under_a_foreign_host(world, client_for):
    world.coord_a.user_permissions.add(*_announcement_permissions())
    client = client_for(world.b)
    client.force_login(world.coord_a)
    before = world.b.announcements.count()

    # Odmowa w ``/cms/`` to przekierowanie na pulpit z komunikatem (tak zamienia ``PermissionDenied``
    # dekorator panelu Wagtaila) — sprawdzamy skutek: komunikat w konkursie B nie powstał.
    assert client.get("/cms/snippets/cms/announcement/add/").status_code in (302, 403)
    client.post(
        "/cms/snippets/cms/announcement/add/",
        {"text": "Obcy komunikat", "level": "info", "starts_at": "2026-01-01 10:00", "is_active": "on"},
    )
    assert world.b.announcements.count() == before


def test_coordinator_has_no_site_settings_of_either_site(world, as_a):
    """Grupa globalna nigdy nie miała ustawień serwisu — i po zawężeniu nie ma ich żadna grupa konkursu."""
    for site_id in (world.a.site_id, world.b.site_id):
        assert as_a.get(f"/cms/settings/cms/sitesettings/{site_id}/").status_code in (302, 403)


def test_collections_management_stays_closed(as_a):
    assert as_a.get("/cms/collections/").status_code in (302, 403)


def _announcement_permissions():
    from django.contrib.auth.models import Permission

    return list(Permission.objects.filter(content_type__app_label="cms", content_type__model="announcement"))


# --- raporty i dziennik ---------------------------------------------------------------------------


def test_site_history_hides_foreign_log_entries(world, as_a):
    for obj in (world.image_a, world.image_b, world.doc_a, world.doc_b, world.page_a, world.page_b):
        log(obj, "wagtail.edit", user=world.coord_b if TITLE_B in obj.title else world.coord_a)

    assert_scoped(as_a.get("/cms/reports/site-history/"))


def test_log_entry_queryset_is_scoped(world):
    """Podmiana ``viewable_by_user`` nadal działa — czerwony test po aktualizacji Wagtaila."""
    from wagtail.models import ModelLogEntry

    log(world.image_a, "wagtail.edit", user=world.coord_a)
    log(world.image_b, "wagtail.edit", user=world.coord_b)
    labels = set(ModelLogEntry.objects.viewable_by_user(world.coord_a).values_list("label", flat=True))

    assert world.image_a.title in labels
    assert world.image_b.title not in labels


def test_locked_pages_report_hides_foreign_pages(world, as_a):
    for page in (world.page_a, world.page_b):
        page.locked, page.locked_by = True, world.coord_b
        page.save()

    text = body(as_a.get("/cms/reports/locked/"))
    assert TITLE_B not in text


def test_aging_pages_report_hides_foreign_pages(world, as_a):
    for page in (world.page_a, world.page_b):
        page.save_revision(user=world.coord_a).publish()

    assert_scoped(as_a.get("/cms/reports/aging-pages/"))


def test_page_types_report_is_closed_with_several_sites(world, as_a):
    assert as_a.get("/cms/reports/page-types-usage/").status_code == 403
    assert "page-types-usage" not in body(as_a.get("/cms/"))


def test_dashboard_does_not_leak_foreign_titles(world, as_a):
    for page in (world.page_a, world.page_b):
        page.save_revision(user=world.coord_b)

    assert TITLE_B not in body(as_a.get("/cms/"))


# --- konta bez ograniczeń -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["superuser", "super_coordinator"])
def test_unrestricted_accounts_see_both_competitions(world, client_for, kind):
    user = UserFactory(
        email=f"{kind}@example.test", is_superuser=kind == "superuser", is_staff=kind == "superuser"
    )
    if kind == "super_coordinator":
        super_coordinator.grant(user)
    client = client_for(world.a)
    client.force_login(user)

    for url in ("/cms/images/", "/cms/documents/", "/cms/images/chooser/", "/cms/api/main/images/"):
        text = body(client.get(url, {"fields": "title"} if "api" in url else None))
        assert TITLE_A in text and TITLE_B in text, url
    assert client.get(f"/cms/pages/{world.page_b.pk}/edit/").status_code == 200
    search = body(client.get("/cms/pages/search/", {"q": "Tajemnica"}))
    assert TITLE_A in search and TITLE_B in search
    announcements = body(client.get("/cms/snippets/cms/announcement/"))
    assert TITLE_A in announcements and TITLE_B in announcements
    assert client.get(f"/cms/snippets/cms/announcement/edit/{world.ann_b.pk}/").status_code == 200
    assert client.get(f"/cms/settings/cms/sitesettings/{world.b.site_id}/").status_code == 200
    assert client.get(f"/cms/choose-page/{world.b.site.root_page_id}/").status_code == 200


def test_super_coordinator_does_not_get_platform_administration(world, client_for):
    user = UserFactory(email="super-bez-admina@example.test")
    super_coordinator.grant(user)
    client = client_for(world.a)
    client.force_login(user)

    assert client.get("/cms/users/").status_code in (302, 403)
    assert client.get("/cms/groups/").status_code in (302, 403)
    assert client.get("/cms/sites/").status_code in (302, 403)
    assert client.get("/admin/").status_code == 302  # logowanie do /admin/, bo is_staff=False
