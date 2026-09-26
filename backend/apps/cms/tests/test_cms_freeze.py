"""Zamrożenie edycji stron Wagtaila po przełączeniu na django CMS (DJ-02 § 1.2 D9, reguła S13).

Sprawdzamy trzy rzeczy: (1) **każdy** widok panelu zmieniający stan strony odpowiada 403, zanim
cokolwiek zapisze – także superużytkownikowi; (2) strony-dane (``warsztaty``, ``PartnersPage``)
i to, czego zamrożenie nie dotyczy (ustawienia, komunikaty, media), działają dalej; (3) przełącznik
(``cms_freeze``) i jego pamięć na 10 s. Wyłączone zamrożenie = zachowanie Wagtaila bez zmian:
dowodem jest cały pakiet ``apps/cms/tests``, a tu – test testera i blokady wprost.
"""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import set_script_prefix
from wagtail.fields import StreamField
from wagtail.models import Page, PagePermissionTester
from wagtail.test.utils.form_data import nested_form_data, querydict_from_html, streamfield

from apps.accounts.tests.factories import DEFAULT_PASSWORD, CoordinatorFactory, UserFactory
from apps.cms import freeze
from apps.cms.models import ContentPage, EditingFreeze, HomePage, PartnersPage, SiteSettings
from apps.cms.permissions import cms_abilities

pytestmark = pytest.mark.django_db

DENIED_MARK = b"data-cms-freeze-denied"


# --- świat --------------------------------------------------------------------------------------


@pytest.fixture
def home():
    return HomePage.objects.get()


@pytest.fixture
def page(home):
    return home.add_child(instance=ContentPage(title="Kontakt", slug="kontakt-zamrozenie"))


@pytest.fixture
def other_page(home):
    return home.add_child(instance=ContentPage(title="O nas", slug="o-nas-zamrozenie"))


@pytest.fixture
def workshops(home):
    existing = ContentPage.objects.filter(slug="warsztaty").first()
    return existing or home.add_child(instance=ContentPage(title="Warsztaty", slug="warsztaty"))


@pytest.fixture
def partners(home):
    existing = PartnersPage.objects.first()
    return existing or home.add_child(instance=PartnersPage(title="Partnerzy", slug="partnerzy"))


@pytest.fixture
def operator():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def admin_client(client, operator):
    assert client.login(email=operator.email, password=DEFAULT_PASSWORD)
    return client


@pytest.fixture
def frozen():
    return freeze.set_frozen(True, message="Edycja przeniesiona do django CMS.", changed_by="test")


def _denied(response) -> bool:
    return response.status_code == 403 and DENIED_MARK in response.content


# --- S13: każdy widok zmieniający stan strony ------------------------------------------------------


def _tree_and_content_requests(home, page, other_page):
    """(metoda, adres) każdego widoku panelu, który zmienia stan strony."""
    p, h, o = page.pk, home.pk, other_page.pk
    return [
        # drzewo
        ("get", f"/cms/pages/add/cms/contentpage/{h}/"),
        ("post", f"/cms/pages/add/cms/contentpage/{h}/"),
        ("post", f"/cms/pages/add/cms/contentpage/{h}/preview/"),
        ("get", f"/cms/pages/{h}/add_subpage/"),
        ("get", "/cms/pages/create/cms/contentpage"),
        ("get", f"/cms/pages/{p}/copy/"),
        ("post", f"/cms/pages/{p}/copy/"),
        ("get", f"/cms/pages/{p}/move/"),
        ("post", f"/cms/pages/{p}/move/{o}/confirm/"),
        ("post", f"/cms/pages/{p}/set_position/?position=0"),
        ("get", f"/cms/pages/{p}/delete/"),
        ("post", f"/cms/pages/{p}/delete/"),
        ("post", f"/cms/pages/{p}/convert_alias/"),
        ("post", f"/cms/translation/submit/page/{p}/"),
        # treść
        ("post", f"/cms/pages/{p}/edit/"),
        ("get", f"/cms/pages/{p}/unpublish/"),
        ("post", f"/cms/pages/{p}/unpublish/"),
        ("post", f"/cms/pages/{p}/lock/"),
        ("post", f"/cms/pages/{p}/unlock/"),
        ("get", f"/cms/pages/{p}/privacy/"),
        ("post", f"/cms/pages/{p}/privacy/"),
        ("post", f"/cms/pages/{p}/revisions/1/revert/"),
        ("post", f"/cms/pages/{p}/revisions/1/unschedule/"),
        ("post", f"/cms/pages/workflow/action/{p}/approve/1/"),
        ("post", f"/cms/pages/workflow/collect_action_data/{p}/approve/1/"),
        ("get", f"/cms/pages/workflow/confirm_cancellation/{p}/"),
        # akcje zbiorcze
        ("post", f"/cms/bulk/wagtailcore/page/delete/?id={p}"),
        ("post", f"/cms/bulk/wagtailcore/page/publish/?id={p}"),
        ("post", f"/cms/bulk/wagtailcore/page/unpublish/?id={p}"),
        ("get", f"/cms/bulk/wagtailcore/page/move/?id={p}"),
    ]


def test_every_state_changing_page_view_is_refused_for_a_superuser(
    admin_client, frozen, home, page, other_page
):
    before = Page.objects.count()

    for method, url in _tree_and_content_requests(home, page, other_page):
        response = getattr(admin_client, method)(url)
        assert _denied(response), (method, url, response.status_code)

    page.refresh_from_db()
    assert Page.objects.count() == before
    assert page.live and page.title == "Kontakt" and not page.locked
    assert page.get_parent().pk == home.pk


def test_a_coordinator_is_refused_too(client, frozen, page):
    coordinator = CoordinatorFactory()
    assert client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    assert _denied(client.post(f"/cms/pages/{page.pk}/delete/"))
    assert _denied(client.post(f"/cms/pages/{page.pk}/edit/", {"title": "Podmieniony"}))
    assert Page.objects.filter(pk=page.pk).exists()


def test_the_editor_is_a_read_only_view_and_saving_it_is_refused(admin_client, frozen, page):
    response = admin_client.get(f"/cms/pages/{page.pk}/edit/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "content-locked" in html
    assert "Strona tylko do odczytu." in html
    # Menu akcji: sama informacja o blokadzie – bez „Zapisz szkic” i „Opublikuj”.
    assert 'name="action-publish"' not in html
    data = querydict_from_html(html, form_id="page-edit-form").copy()
    data["title"] = "Podmieniony"
    data["action-publish"] = "action-publish"

    assert _denied(admin_client.post(f"/cms/pages/{page.pk}/edit/", data))
    page.refresh_from_db()
    assert page.title == "Kontakt"


def test_autosave_gets_a_json_error_in_the_wagtail_shape(admin_client, frozen, page):
    response = admin_client.post(
        f"/cms/pages/{page.pk}/edit/", {"title": "X"}, HTTP_ACCEPT="application/json"
    )

    assert response.status_code == 403
    body = json.loads(response.content)
    assert body["success"] is False
    assert body["error_code"] == "editing_frozen"
    assert "django CMS" in body["error_message"]


def test_read_only_views_stay_open(admin_client, frozen, home, page):
    for url in (
        "/cms/",
        f"/cms/pages/{home.pk}/",
        f"/cms/pages/{page.pk}/edit/",
        f"/cms/pages/{page.pk}/history/",
        f"/cms/pages/{page.pk}/usage/",
        "/cms/pages/search/?q=Kontakt",
    ):
        assert admin_client.get(url).status_code == 200, url


def test_anonymous_request_goes_to_the_login_not_to_the_freeze_screen(client, frozen, page):
    response = client.post(f"/cms/pages/{page.pk}/delete/")

    assert response.status_code == 302
    assert "/cms/login/" in response.headers["Location"]


def test_missing_page_is_left_to_wagtail(admin_client, frozen):
    assert admin_client.post("/cms/pages/999999/unpublish/").status_code == 404


def test_bulk_actions_on_other_models_are_not_frozen(admin_client, frozen):
    response = admin_client.get("/cms/bulk/wagtailimages/image/delete/?id=999999")

    assert DENIED_MARK not in response.content
    assert response.status_code != 403


# --- przyciski --------------------------------------------------------------------------------------


def test_listing_hides_tree_and_publishing_buttons(admin_client, home, page):
    listing = f"/cms/pages/{home.pk}/"
    delete_url = f"/cms/pages/{page.pk}/delete/"
    move_url = f"/cms/pages/{page.pk}/move/"
    edit_url = f"/cms/pages/{page.pk}/edit/"

    html = admin_client.get(listing).content.decode()
    assert delete_url in html and move_url in html

    freeze.set_frozen(True)
    html = admin_client.get(listing).content.decode()
    assert delete_url not in html
    assert move_url not in html
    assert f"/cms/pages/{page.pk}/copy/" not in html
    assert f"/cms/pages/{page.pk}/unpublish/" not in html
    assert f"/cms/pages/{home.pk}/add_subpage/" not in html
    assert edit_url in html  # podgląd tylko do odczytu zostaje


# --- wyjątki D9: strony-dane --------------------------------------------------------------------------


def _publish_with_title(client, page, title, **changes):
    """Formularz edycji z ekranu (jak przeglądarka) + tytuł; StreamFieldy (składa je JS) – puste."""
    html = client.get(f"/cms/pages/{page.pk}/edit/").content.decode()
    data = querydict_from_html(html, form_id="page-edit-form").copy()
    for field in page.specific_class._meta.get_fields():
        if isinstance(field, StreamField) and f"{field.name}-count" not in data:
            data.update(nested_form_data({field.name: streamfield([])}))
    data["title"] = title
    for key, value in changes.items():
        data[key] = value
    data["action-publish"] = "action-publish"
    return client.post(f"/cms/pages/{page.pk}/edit/", data)


@pytest.mark.parametrize("fixture", ["workshops", "partners"])
def test_data_pages_stay_editable_and_publishable(request, admin_client, frozen, fixture):
    data_page = request.getfixturevalue(fixture)

    editor = admin_client.get(f"/cms/pages/{data_page.pk}/edit/")
    assert editor.status_code == 200
    html = editor.content.decode()
    assert "content-locked" not in html
    assert "Tę stronę edytujesz dalej w Wagtailu" in html

    response = _publish_with_title(admin_client, data_page, "Nowy tytuł")

    assert response.status_code == 302, response.content[:500]
    data_page.refresh_from_db()
    assert data_page.title == "Nowy tytuł"
    assert data_page.live
    assert admin_client.get(f"/cms/pages/{data_page.pk}/unpublish/").status_code == 200


def test_data_page_keeps_its_slug(admin_client, frozen, workshops):
    response = _publish_with_title(admin_client, workshops, "Warsztaty", slug="warsztaty-online")

    assert _denied(response)
    workshops.refresh_from_db()
    assert workshops.slug == "warsztaty"


@pytest.mark.parametrize("fixture", ["workshops", "partners"])
def test_data_pages_cannot_leave_the_tree(request, admin_client, frozen, other_page, fixture):
    data_page = request.getfixturevalue(fixture)

    for method, url in (
        ("post", f"/cms/pages/{data_page.pk}/delete/"),
        ("post", f"/cms/pages/{data_page.pk}/copy/"),
        ("post", f"/cms/pages/{data_page.pk}/move/{other_page.pk}/confirm/"),
        ("get", f"/cms/pages/{data_page.pk}/add_subpage/"),
    ):
        assert _denied(getattr(admin_client, method)(url)), (method, url)
    assert Page.objects.filter(pk=data_page.pk).exists()


def test_other_content_pages_are_not_exempt(workshops, page):
    assert freeze.is_exempt(workshops)
    assert not freeze.is_exempt(page)
    # Ten sam slug pod innym typem niż strona treści nie jest wyjątkiem.
    assert not freeze.is_exempt(Page(slug="warsztaty"))


# --- czego zamrożenie nie dotyczy ------------------------------------------------------------------


def test_site_settings_media_and_snippets_stay_editable(admin_client, frozen):
    site_id = SiteSettings.for_site(HomePage.objects.get().get_site()).site_id
    settings_url = f"/cms/settings/cms/sitesettings/{site_id}/"
    html = admin_client.get(settings_url).content.decode()
    data = querydict_from_html(html, form_id="w-editor-form").copy()
    data["site_name"] = "Serwis po zamrożeniu"

    response = admin_client.post(settings_url, data)

    assert response.status_code == 302, response.content[:500]
    assert SiteSettings.objects.get(site_id=site_id).site_name == "Serwis po zamrożeniu"
    for url in (
        "/cms/images/",
        "/cms/images/add/",
        "/cms/documents/",
        "/cms/documents/add/",
        "/cms/snippets/cms/announcement/",
        "/cms/snippets/cms/announcement/add/",
        "/cms/collections/add/",
    ):
        assert admin_client.get(url).status_code == 200, url


# --- baner ----------------------------------------------------------------------------------------------


def test_banner_only_while_frozen(admin_client):
    assert b"cms-freeze-banner-data" not in admin_client.get("/cms/").content

    freeze.set_frozen(True, message="Edytuj w django CMS.")
    html = admin_client.get("/cms/").content.decode()

    assert "cms-freeze-banner-data" in html
    assert "Edytuj w django CMS." in html
    assert "/djcms/admin/" in html


def test_banner_message_is_never_html(admin_client):
    freeze.set_frozen(True, message="<img src=x onerror=alert(1)>")

    html = admin_client.get("/cms/").content.decode()

    assert "<img src=x" not in html
    assert "\\u003Cimg src=x onerror=alert(1)\\u003E" in html


def test_djcms_link_follows_the_script_prefix():
    set_script_prefix("/druga/")
    try:
        assert freeze.djcms_url() == "/druga/djcms/admin/"
    finally:
        set_script_prefix("/")


# --- tester, blokada, macierz uprawnień -------------------------------------------------------------


def test_without_freeze_wagtail_answers_unchanged(operator, page):
    assert freeze.frozen_permission_tester(page, operator) is None
    assert freeze.freeze_lock(page) is None
    assert type(page.permissions_for_user(operator)) is PagePermissionTester
    assert page.get_lock() is None


def test_frozen_tester_and_lock(operator, frozen, page, workshops):
    tester = Page.objects.get(pk=page.pk).permissions_for_user(operator)  # strona bazowa → specyficzna

    assert isinstance(tester, freeze.FrozenPagePermissionTester)
    assert tester.can_edit() and tester.can_view_revisions()
    assert not any(
        (
            tester.can_publish(),
            tester.can_unpublish(),
            tester.can_delete(),
            tester.can_move(),
            tester.can_copy(),
            tester.can_add_subpage(),
            tester.can_reorder_children(),
            tester.can_lock(),
            tester.can_unlock(),
            tester.can_set_view_restrictions(),
        )
    )
    assert isinstance(page.get_lock(), freeze.EditingFreezeLock)
    assert page.get_lock().for_user(operator)

    data_tester = workshops.permissions_for_user(operator)
    assert data_tester.can_edit() and data_tester.can_publish() and data_tester.can_unpublish()
    assert not data_tester.can_delete() and not data_tester.can_move() and not data_tester.can_copy()
    assert workshops.get_lock() is None


def test_ability_matrix_ignores_the_freeze(page):
    coordinator = CoordinatorFactory()
    before = cms_abilities(coordinator)

    freeze.set_frozen(True)

    assert cms_abilities(coordinator) == before
    assert ("page", page.pk, "delete") in before
    assert freeze.is_frozen()  # ``ignoring`` nie przecieka poza blok


# --- przełącznik -------------------------------------------------------------------------------------------


def test_no_row_means_not_frozen():
    assert not EditingFreeze.objects.exists()
    assert freeze.freeze_state() == freeze.INACTIVE


def test_the_row_is_a_singleton():
    with pytest.raises(IntegrityError), transaction.atomic():
        EditingFreeze.objects.create(id=2, active=True)


def test_command_on_off_status(capsys):
    call_command("cms_freeze", "on", "--message", "Edycja w django CMS.", "--by", "operator")

    row = EditingFreeze.objects.get()
    assert row.active and row.message == "Edycja w django CMS." and row.changed_by == "operator"
    assert row.changed_at is not None
    call_command("cms_freeze", "status")  # kod 0 = zamrożone
    assert "WŁĄCZONE – Edycja w django CMS." in capsys.readouterr().out

    call_command("cms_freeze", "off")
    assert not EditingFreeze.objects.get().active
    with pytest.raises(SystemExit) as exit_info:
        call_command("cms_freeze", "status")
    assert exit_info.value.code == 1
    assert "WYŁĄCZONE" in capsys.readouterr().out


def test_command_default_message_and_operator():
    call_command("cms_freeze", "on")

    row = EditingFreeze.objects.get()
    assert row.message == ""
    assert freeze.freeze_state().banner_message == freeze.DEFAULT_MESSAGE
    assert row.changed_by.startswith("manage.py cms_freeze")


def test_state_is_cached_for_ten_seconds(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(freeze.time, "monotonic", lambda: clock[0])
    freeze.set_frozen(True)
    # Zmiana z innego procesu: wiersz zmienia się bez sygnału w tym procesie.
    EditingFreeze.objects.update(active=False)

    clock[0] += freeze.CACHE_TTL_SECONDS - 1
    assert freeze.is_frozen()
    clock[0] += 2
    assert not freeze.is_frozen()


def test_saving_the_row_in_this_process_is_seen_at_once():
    freeze.set_frozen(False)
    assert not freeze.is_frozen()

    row = EditingFreeze.objects.get()
    row.active = True
    row.save()

    assert freeze.is_frozen()


def test_the_middleware_passes_everything_when_not_frozen(admin_client, page):
    assert admin_client.get(f"/cms/pages/{page.pk}/delete/").status_code == 200
    assert admin_client.get(f"/cms/pages/{page.pk}/move/").status_code == 200
    response = _publish_with_title(admin_client, page, "Kontakt – nowy")
    assert response.status_code == 302
    page.refresh_from_db()
    assert page.title == "Kontakt – nowy"
