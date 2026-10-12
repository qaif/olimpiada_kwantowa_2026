"""Cele przekierowań tylko na hosty platformy, inny host – z konta platformy (audyt 2026-10-10)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from apps.seo.models import Redirect
from apps.seo.targets import PATCH_MARKER, is_platform_target, validate_target

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(competition_site):
    return competition_site.site


@pytest.fixture
def scoped_editor(django_user_model, site):
    """Redaktor jednego konkursu (``GlobalPagePermission`` z listą witryn) z prawami do przekierowań."""
    from cms.models import GlobalPagePermission
    from django.contrib.auth.models import Permission

    user = django_user_model.objects.create_user(
        "red@example.com", "red@example.com", "haslo-redaktora-123", is_staff=True
    )
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="dj_seo"))
    permission = GlobalPagePermission.objects.create(user=user, can_change=True)
    permission.sites.add(site)
    return user


@pytest.fixture
def platform_editor(django_user_model):
    """Konto z grupą ``redakcja:platforma`` (uprawnienie bez listy witryn) – jak po SSO z ``platform``."""
    from django.contrib.auth.models import Permission

    from apps.sites.permissions import ensure_platform_group

    user = django_user_model.objects.create_user("plat@example.com", "plat@example.com", is_staff=True)
    user.groups.add(ensure_platform_group())
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="dj_seo"))
    return user


@pytest.mark.parametrize(
    "target",
    [
        "/regulamin/",
        "regulamin/",
        "",
        "https://testserver/x/",  # ALLOWED_HOSTS
        "https://nowy.olimpiada.example/",  # ALLOWED_HOSTS: ``.olimpiada.example``
        "https://OLIMPIADA.example:443/x",  # adres publiczny konkursu, wielkość liter i port
    ],
)
def test_platform_targets(target):
    assert is_platform_target(target)


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        "http://evil.example",
        "/\\evil.example/",
        "https://evil.example\\@testserver/",
        "javascript:alert(1)",
        "https://olimpiada.example.evil.example/",
        "https://[::1/",
    ],
)
def test_foreign_targets(target):
    assert not is_platform_target(target)


def test_host_of_an_active_competition_counts_but_not_of_a_retired_one(make_competition):
    make_competition("fizyka", hosts=["fizyka-konkurs.pl"])
    make_competition("stary", hosts=["stary-konkurs.pl"], is_active=False, public_origin="https://stary.pl")
    assert is_platform_target("https://fizyka-konkurs.pl/zapisy/")
    assert not is_platform_target("https://stary-konkurs.pl/")
    assert not is_platform_target("https://stary.pl/")


def test_model_clean_refuses_foreign_host_unless_allowed(site):
    with pytest.raises(ValidationError, match="host platformy"):
        Redirect(site=site, old_path="/stary", new_path="https://evil.example/").full_clean()
    item = Redirect(site=site, old_path="/stary", new_path="https://evil.example/")
    item.allow_foreign_host = True
    item.full_clean()


def test_admin_scoped_editor_cannot_point_outside_the_platform(client, scoped_editor, site):
    client.force_login(scoped_editor)
    data = {"site": site.pk, "old_path": "/stary/", "new_path": "https://evil.example/", "is_permanent": "on"}
    response = client.post("/djcms/admin/dj_seo/redirect/add/", data)
    assert response.status_code == 200
    assert "host platformy" in response.content.decode()
    assert not Redirect.objects.exists()

    data["new_path"] = "https://olimpiada.example/regulamin/"
    assert client.post("/djcms/admin/dj_seo/redirect/add/", data).status_code == 302
    assert Redirect.objects.get().new_path == "https://olimpiada.example/regulamin/"


@pytest.mark.parametrize("who", ["superuser", "platform_editor"])
def test_admin_platform_account_may_point_anywhere(client, site, request, who):
    client.force_login(request.getfixturevalue(who))
    response = client.post(
        "/djcms/admin/dj_seo/redirect/add/",
        {
            "site": site.pk,
            "old_path": "/partner/",
            "new_path": "https://partner.example/",
            "is_permanent": "on",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    assert Redirect.objects.get().new_path == "https://partner.example/"


# --- pole „przekierowanie” strony django CMS ---------------------------------------------------------


def _clean_page_redirect(value, user):
    from cms.admin.forms import ChangePageForm

    form = SimpleNamespace(cleaned_data={"redirect": value}, _request=SimpleNamespace(user=user))
    return ChangePageForm.clean_redirect(form)


def test_page_redirect_validation_is_installed():
    from cms.admin.forms import ChangePageForm

    assert getattr(ChangePageForm, PATCH_MARKER, False)


def test_page_redirect_field_refuses_foreign_host_for_a_competition_editor(scoped_editor):
    for target in ("https://evil.example/", "//evil.example/x"):
        with pytest.raises(ValidationError, match="host platformy"):
            _clean_page_redirect(target, scoped_editor)
    assert _clean_page_redirect("/regulamin/", scoped_editor) == "/regulamin/"
    assert (
        _clean_page_redirect("https://olimpiada.example/x/", scoped_editor) == "https://olimpiada.example/x/"
    )
    assert _clean_page_redirect("", scoped_editor) == ""


def test_page_redirect_field_platform_account_may_point_anywhere(superuser, platform_editor):
    assert _clean_page_redirect("https://partner.example/", superuser) == "https://partner.example/"
    assert _clean_page_redirect("https://partner.example/", platform_editor) == "https://partner.example/"


def test_form_without_request_is_treated_as_a_competition_editor():
    with pytest.raises(ValidationError):
        validate_target("https://evil.example/", None)
