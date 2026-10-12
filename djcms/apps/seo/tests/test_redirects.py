"""Przekierowania witryn konkursów (DJ-02 § 8): model, warstwa na 404, automat przy zmianie adresu, panel.

Kolejność prób i normalizacja jak ``wagtail.contrib.redirects`` (+ prefiks ścieżki konkursu jak
``apps.cms.redirects`` aplikacji głównej); wpisy importu zapisuje ``apps.importer`` (DJ-02e).
"""

import pytest
from django.apps import apps
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError

from apps.seo.models import Redirect, RedirectSource, normalise_path


@pytest.fixture
def site(competition_site):
    return competition_site.site


def _redirect(site, old, new, **extra):
    return Redirect.objects.create(site=site, old_path=normalise_path(old), new_path=new, **extra)


# --- model ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/regulamin/", "/regulamin"),
        ("regulamin", "/regulamin"),
        ("/", "/"),
        (" /a/b/ ", "/a/b"),
        ("/a/?b=2&a=1", "/a?a=1&b=2"),
        ("/a;y;x", "/a;x;y"),
        ("https://stara.example/x/", "/x"),
    ],
)
def test_normalise_path_like_wagtail(raw, expected):
    assert normalise_path(raw) == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "target", ["//evil.example/", "javascript:alert(1)", "ftp://x.example/", "/a b/", "", "/\\x"]
)
def test_unsafe_targets_are_rejected(site, target):
    with pytest.raises(ValidationError):
        Redirect(site=site, old_path="/stary", new_path=target).full_clean()


@pytest.mark.django_db
def test_clean_normalises_and_rejects_self_redirect(site):
    item = Redirect(site=site, old_path="/stary/", new_path="https://nowy.olimpiada.example/")
    item.full_clean()
    assert item.old_path == "/stary"
    with pytest.raises(ValidationError):
        Redirect(site=site, old_path="/stary", new_path="/stary/").full_clean()


def test_importer_contract():
    """``apps.importer.services.store_redirects`` zapisuje te pola i to źródło (DJ-02e)."""
    model = apps.get_model("dj_seo", "Redirect")
    assert {"site", "old_path", "new_path", "is_permanent", "source"} <= {f.name for f in model._meta.fields}
    assert RedirectSource.IMPORT == "import"


# --- warstwa ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_permanent_redirect_on_404_with_and_without_slash(client, site, make_page):
    make_page("Regulamin", "regulamin-2026")
    _redirect(site, "/regulamin", "/regulamin-2026/", source=RedirectSource.IMPORT)
    for path in ("/regulamin", "/regulamin/"):
        response = client.get(path)
        assert response.status_code == 301, path
        assert response["Location"] == "/regulamin-2026/"


@pytest.mark.django_db
def test_temporary_redirect_and_absolute_target(client, site):
    _redirect(site, "/tymczasowy", "/cel/", is_permanent=False)
    _redirect(site, "/zewnetrzny", "https://inny.example/x")
    assert client.get("/tymczasowy/").status_code == 302
    response = client.get("/zewnetrzny/")
    assert response.status_code == 301
    assert response["Location"] == "https://inny.example/x"


@pytest.mark.django_db
def test_query_specific_first_then_without_query(client, site):
    _redirect(site, "/lista?rok=2025", "/archiwum/2025/")
    _redirect(site, "/lista", "/aktualnosci/")
    assert client.get("/lista/?rok=2025")["Location"] == "/archiwum/2025/"
    assert client.get("/lista/?rok=2024")["Location"] == "/aktualnosci/"


@pytest.mark.django_db
def test_live_page_wins_over_redirect(client, site, make_page):
    make_page("O olimpiadzie", "o-olimpiadzie")
    _redirect(site, "/o-olimpiadzie", "/gdzie-indziej/")
    assert client.get("/o-olimpiadzie/").status_code == 200


@pytest.mark.django_db
def test_redirects_are_per_site(client, make_competition):
    other = make_competition("fizyka", hosts=["fizyka.example"])
    _redirect(other.site, "/stary", "/nowy/")
    assert client.get("/stary/").status_code == 404
    assert client.get("/stary/", HTTP_HOST="fizyka.example")["Location"] == "/nowy/"


@pytest.mark.django_db
def test_under_path_prefix_lookup_without_prefix_and_relative_target_with_prefix(
    client, make_competition, competition_site
):
    competition_site.hosts_path_prefixes = True
    competition_site.save(update_fields=["hosts_path_prefixes"])
    druga = make_competition("druga", routing_mode="PATH", path_prefix="druga")
    _redirect(druga.site, "/stary", "/nowy/")
    _redirect(druga.site, "/zewn", "https://inny.example/")
    assert client.get("/druga/stary/")["Location"] == "/druga/nowy/"
    assert client.get("/druga/zewn/")["Location"] == "https://inny.example/"
    # Ten sam adres u gospodarza – bez przekierowania (inna witryna).
    assert client.get("/stary/").status_code == 404


@pytest.mark.django_db
def test_encoded_and_unicode_paths(client, site):
    _redirect(site, "/zażółć", "/cel/")
    assert client.get("/za%C5%BC%C3%B3%C5%82%C4%87/")["Location"] == "/cel/"


@pytest.mark.django_db
def test_app_paths_are_not_looked_up(client, site):
    _redirect(site, "/djcms/stary", "/cel/")
    assert client.get("/djcms/stary/").status_code == 404


@pytest.mark.django_db
def test_redirect_skips_the_404_frame(client, site, main_api):
    _redirect(site, "/stary", "/nowy/")
    client.get("/stary/")
    assert main_api.calls("chrome") == 0  # ``process_exception`` – przed renderowaniem ramy 404


# --- automat: zmiana adresu opublikowanej strony ----------------------------------------------------


def _republish_with_slug(page, slug, user):
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    published = PageContent.objects.get(page=page, language="pl")  # opublikowana (versioning)
    draft_version = Version.objects.get_for_content(published).copy(user)
    draft = draft_version.content
    draft.slug = slug
    draft.save()
    draft_version.publish(user)


@pytest.mark.django_db
def test_slug_change_creates_auto_redirects_for_page_and_descendants(client, make_page, superuser, site):
    parent = make_page("Dokumenty", "dokumenty")
    make_page("Regulamin", "regulamin", parent=parent)
    _republish_with_slug(parent, "pliki", superuser)

    redirects = dict(Redirect.objects.filter(site=site).values_list("old_path", "new_path"))
    assert redirects == {"/dokumenty": "/pliki/", "/dokumenty/regulamin": "/pliki/regulamin/"}
    assert set(Redirect.objects.values_list("source", flat=True)) == {RedirectSource.AUTO}
    assert client.get("/dokumenty/regulamin/")["Location"] == "/pliki/regulamin/"
    assert client.get("/pliki/regulamin/").status_code == 200


@pytest.mark.django_db
def test_changing_back_removes_the_dead_redirect_and_repoints_chains(make_page, superuser, site):
    page = make_page("Dokumenty", "dokumenty")
    _republish_with_slug(page, "pliki", superuser)
    _republish_with_slug(page, "materialy", superuser)
    assert dict(Redirect.objects.values_list("old_path", "new_path")) == {
        "/dokumenty": "/materialy/",
        "/pliki": "/materialy/",
    }
    _republish_with_slug(page, "dokumenty", superuser)
    assert dict(Redirect.objects.values_list("old_path", "new_path")) == {
        "/pliki": "/dokumenty/",
        "/materialy": "/dokumenty/",
    }


@pytest.mark.django_db
def test_move_creates_auto_redirect(make_page, site):
    first = make_page("Pierwsza", "pierwsza")
    second = make_page("Druga", "druga")
    child = make_page("Dziecko", "dziecko", parent=first)
    child.move_page(second, position="first-child")
    assert dict(Redirect.objects.values_list("old_path", "new_path")) == {
        "/pierwsza/dziecko": "/druga/dziecko/"
    }


@pytest.mark.django_db
def test_manual_redirect_is_not_overwritten(make_page, superuser, site):
    page = make_page("Dokumenty", "dokumenty")
    _redirect(site, "/dokumenty", "https://archiwum.example/", source=RedirectSource.MANUAL)
    _republish_with_slug(page, "pliki", superuser)
    kept = Redirect.objects.get(old_path="/dokumenty")
    assert (kept.new_path, kept.source) == ("https://archiwum.example/", RedirectSource.MANUAL)


@pytest.mark.django_db
def test_unpublish_creates_nothing(make_page, superuser):
    from cms.models import PageContent
    from djangocms_versioning.models import Version

    page = make_page("Dokumenty", "dokumenty")
    Version.objects.get_for_content(PageContent.admin_manager.get(page=page, language="pl")).unpublish(
        superuser
    )
    assert not Redirect.objects.exists()


@pytest.mark.django_db
def test_auto_redirect_failure_does_not_break_publishing(make_page, superuser, monkeypatch):
    from apps.seo import auto

    def _boom(*args, **kwargs):
        raise RuntimeError("awaria testowa")

    page = make_page("Dokumenty", "dokumenty")
    monkeypatch.setattr(auto, "record_changes", _boom)
    _republish_with_slug(page, "pliki", superuser)
    assert page.get_path("pl") == "pliki"


# --- panel: zakres witryn redaktora ------------------------------------------------------------------


@pytest.fixture
def scoped_editor(django_user_model, site):
    from cms.models import GlobalPagePermission
    from django.contrib.auth.models import Permission

    user = django_user_model.objects.create_user(
        "red@example.com", "red@example.com", "haslo-redaktora-123", is_staff=True
    )
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="dj_seo"))
    permission = GlobalPagePermission.objects.create(user=user, can_change=True)
    permission.sites.add(site)
    return user


@pytest.mark.django_db
def test_admin_is_scoped_to_editor_sites(client, scoped_editor, site, make_competition):
    other = make_competition("fizyka", hosts=["fizyka.example"])
    own = _redirect(site, "/moj", "/cel/")
    foreign = _redirect(other.site, "/obcy", "/cel/")
    client.force_login(scoped_editor)
    listing = client.get("/djcms/admin/dj_seo/redirect/").content.decode()
    assert "/moj" in listing and "/obcy" not in listing
    assert client.get(f"/djcms/admin/dj_seo/redirect/{own.pk}/change/").status_code == 200
    assert client.get(f"/djcms/admin/dj_seo/redirect/{foreign.pk}/change/").status_code != 200
    add = client.get("/djcms/admin/dj_seo/redirect/add/").content.decode()
    assert f'<option value="{site.pk}"' in add
    assert f'<option value="{other.site.pk}"' not in add


@pytest.mark.django_db
def test_admin_save_marks_manual_and_normalises(client, superuser, site):
    client.force_login(superuser)
    response = client.post(
        "/djcms/admin/dj_seo/redirect/add/",
        {"site": site.pk, "old_path": "/Stary/Adres/", "new_path": "/nowy/", "is_permanent": "on"},
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    item = Redirect.objects.get()
    assert (item.old_path, item.source) == ("/Stary/Adres", RedirectSource.MANUAL)


@pytest.mark.django_db
def test_platform_permission_without_sites_sees_everything(django_user_model, site, make_competition):
    from cms.models import GlobalPagePermission

    from apps.seo.admin import editable_site_ids

    user = django_user_model.objects.create_user(
        "plat@example.com", "plat@example.com", "x" * 16, is_staff=True
    )
    assert editable_site_ids(user) == set()
    GlobalPagePermission.objects.create(user=user, can_change=True)
    assert editable_site_ids(user) is None
    assert Site.objects.count() >= 1
