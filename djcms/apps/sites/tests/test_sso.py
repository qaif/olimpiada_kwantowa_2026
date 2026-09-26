"""Logowanie redaktora tokenem z ``/cms/`` (DJ-02 D6, S11) – ``POST /djcms/sso/``.

Kolejno: udane logowanie (konto bez hasła, grupy z tokenu, sesja z terminem), każda przyczyna
odmowy (403 bez logowania), jednorazowość, zastępowanie uprawnień (także przy odmowie), termin sesji,
hasło tylko dla superużytkownika, wspólny wektor z aplikacją główną i system checki
``dj_sites.E001``/``W001``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from apps.sites import sso
from apps.sites.models import CompetitionSite, SsoNonce

from .conftest import SSO_KEY, grant, make_payload, sign_payload

pytestmark = pytest.mark.django_db

TREE = "/djcms/admin/cms/pagecontent/"


def user_groups(username: str = "web:7") -> set[str]:
    return set(get_user_model().objects.get(username=username).groups.values_list("name", flat=True))


def logged_in(client) -> bool:
    return "_auth_user_id" in client.session


@pytest.fixture
def fizyka(make_competition):
    return make_competition("fizyka", hosts=["fizyka.example"], public_origin="https://fizyka.example")


# --- sukces ---------------------------------------------------------------------------------------


def test_valid_token_logs_in_a_passwordless_staff_account_with_token_groups(client, sso_post):
    response = sso_post()

    assert response.status_code == 302
    assert response["Location"] == TREE
    user = get_user_model().objects.get(username="web:7")
    assert user.is_staff and user.is_active and not user.is_superuser
    assert not user.has_usable_password()
    assert (user.email, user.first_name, user.last_name) == ("koordynator@example.com", "Kora", "Dynator")
    assert user_groups() == {"redakcja:kwantowa"}
    assert logged_in(client)
    assert client.session[sso.SESSION_KEY] > time.time()
    assert client.get(TREE).status_code == 200


def test_session_cookie_does_not_outlive_the_sso_session(client, sso_post, settings):
    settings.DJCMS_SSO_SESSION_SECONDS = 600
    sso_post()
    assert client.session.get_expiry_age() == 600


def test_under_path_prefix_lands_in_the_prefixed_admin(client, sso_post, make_competition):
    CompetitionSite.objects.filter(slug="kwantowa").update(hosts_path_prefixes=True)
    make_competition("druga", routing_mode="PATH", path_prefix="druga", public_origin="https://testserver")

    response = sso_post(prefix="/druga", competitions=[grant("druga")])

    assert response.status_code == 302
    assert response["Location"] == f"/druga{TREE}"
    assert user_groups() == {"redakcja:druga"}


def test_draft_only_ability_gives_the_group_without_publishing(sso_post):
    assert sso_post(competitions=[grant("kwantowa", "edit")]).status_code == 302
    assert user_groups() == {"redakcja:kwantowa:bez-publikacji"}


def test_platform_token_gives_the_platform_group_only(sso_post, fizyka):
    assert sso_post(platform=True, competitions=[grant("kwantowa"), grant("fizyka")]).status_code == 302
    assert user_groups() == {"redakcja:platforma"}


# --- odmowy ---------------------------------------------------------------------------------------


def _refused(client, response):
    assert response.status_code == 403
    assert not logged_in(client)
    assert "Logowanie do django CMS nieudane" in response.content.decode()


def test_get_is_not_allowed(client, sso_key):
    token = sign_payload(make_payload())
    assert client.get("/djcms/sso/", {"token": token}).status_code == 405
    assert not get_user_model().objects.filter(username="web:7").exists()


@pytest.mark.parametrize(
    "origin", [False, "null", "https://evil.example", "http://fizyka.example", "http://testserver:8080"]
)
def test_foreign_or_missing_origin_is_refused(client, sso_post, origin):
    _refused(client, sso_post(origin=origin))
    assert not get_user_model().objects.filter(username="web:7").exists()


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("zły klucz", lambda: sign_payload(make_payload(), key="z" * 40)),
        ("inny host", lambda: sign_payload(make_payload(host="fizyka.example"))),
        (
            "przeterminowany",
            lambda: sign_payload(make_payload(iat=int(time.time()) - 120, exp=int(time.time()) - 60)),
        ),
        (
            "z przyszłości",
            lambda: sign_payload(make_payload(iat=int(time.time()) + 60, exp=int(time.time()) + 90)),
        ),
        ("za długa ważność", lambda: sign_payload(make_payload(exp=int(time.time()) + 3600))),
        ("inny odbiorca", lambda: sign_payload(make_payload(aud="web"))),
        ("inna wersja", lambda: sign_payload(make_payload(v=2))),
        ("zły nonce", lambda: sign_payload(make_payload(nonce="x"))),
        (
            "nieznana umiejętność",
            lambda: sign_payload(make_payload(competitions=[grant("kwantowa", "admin")])),
        ),
        (
            "superuser w tokenie",
            lambda: sign_payload(
                make_payload(competitions=[{"slug": "kwantowa", "abilities": ["edit"], "superuser": True}])
            ),
        ),
        ("zmieniona treść", lambda: sign_payload(make_payload()).replace("v1.", "v1.e", 1)),
        ("śmieci", lambda: "v1.abc.def"),
        ("pusty", lambda: ""),
    ],
)
def test_invalid_tokens_are_refused_without_login(client, sso_post, label, token):  # noqa: ARG001
    _refused(client, sso_post(token=token()))
    assert not get_user_model().objects.filter(username="web:7").exists()


def test_token_is_single_use(client, sso_post):
    token = sign_payload(make_payload())
    assert sso_post(token=token).status_code == 302
    client.logout()

    _refused(client, sso_post(token=token))
    assert SsoNonce.objects.count() == 1


def test_sso_is_off_without_a_key(client, sso_post, settings):
    token = sign_payload(make_payload())
    settings.DJCMS_SSO_KEY = ""
    _refused(client, sso_post(token=token))
    settings.DJCMS_SSO_KEY = "k" * 31
    _refused(client, sso_post(token=token))


def test_blocked_account_stays_blocked(client, sso_post, django_user_model):
    django_user_model.objects.create_user(username="web:7", is_staff=True, is_active=False)
    _refused(client, sso_post())
    assert not django_user_model.objects.get(username="web:7").is_active


# --- zastępowanie uprawnień i odwołanie -----------------------------------------------------------


def test_groups_are_replaced_on_every_login_including_manual_additions(client, sso_post, fizyka):
    from django.contrib.auth.models import Group, Permission

    assert sso_post(competitions=[grant("kwantowa"), grant("fizyka")]).status_code == 302
    assert user_groups() == {"redakcja:kwantowa", "redakcja:fizyka"}
    user = get_user_model().objects.get(username="web:7")
    user.groups.add(Group.objects.create(name="dopisana-recznie"))
    user.user_permissions.add(Permission.objects.get(codename="add_user"))
    client.logout()

    assert sso_post(competitions=[grant("kwantowa")]).status_code == 302

    assert user_groups() == {"redakcja:kwantowa"}
    assert not user.user_permissions.exists()


def test_token_without_this_site_is_refused_but_still_revokes(client, sso_post, fizyka):
    assert sso_post(competitions=[grant("kwantowa"), grant("fizyka")]).status_code == 302
    client.logout()

    _refused(client, sso_post(competitions=[grant("fizyka")]))

    assert user_groups() == {"redakcja:fizyka"}


def test_revocation_takes_effect_in_an_open_session(client, sso_editor, sso_post):
    """Sesja otwarta tokenem z uprawnieniem; kolejny token (z innej karty) już bez niego → panel 403."""
    sso_editor()
    assert client.get(TREE).status_code == 200

    other = type(client)()
    other.post(
        "/djcms/sso/",
        {"token": sign_payload(make_payload(competitions=[]))},
        HTTP_HOST="testserver",
        HTTP_ORIGIN="http://testserver",
    )

    assert user_groups() == set()
    assert client.get(TREE).status_code == 403


def test_superuser_flag_and_password_are_cleared(client, sso_post, django_user_model):
    django_user_model.objects.create_superuser(
        username="web:7", email="x@example.com", password="haslo-12345678"
    )
    assert sso_post().status_code == 302
    user = django_user_model.objects.get(username="web:7")
    assert not user.is_superuser
    assert not user.has_usable_password()


def test_unknown_or_inactive_competition_in_token_gives_nothing(client, sso_post, make_competition):
    make_competition("stary", hosts=["stary.example"], is_active=False)
    assert sso_post(competitions=[grant("kwantowa"), grant("stary"), grant("nie-ma")]).status_code == 302
    assert user_groups() == {"redakcja:kwantowa"}


# --- termin sesji ---------------------------------------------------------------------------------


def test_expired_sso_session_is_logged_out(client, sso_editor):
    sso_editor()
    session = client.session
    session[sso.SESSION_KEY] = int(time.time()) - 1
    session.save()

    response = client.get(TREE)

    assert response.status_code == 302
    assert "/djcms/admin/login/" in response["Location"]
    assert not logged_in(client)


def test_sso_account_without_session_deadline_is_logged_out(client, sso_post, django_user_model):
    assert sso_post().status_code == 302
    client.logout()
    client.force_login(django_user_model.objects.get(username="web:7"))

    assert client.get(TREE).status_code == 302
    assert not logged_in(client)


# --- hasło tylko dla superużytkownika -------------------------------------------------------------


def test_password_login_is_for_the_technical_superuser_only(client, django_user_model, superuser):
    django_user_model.objects.create_user(username="lokalny", password="haslo-redaktora-12345", is_staff=True)
    login = "/djcms/admin/login/"
    refused = client.post(login, {"username": "lokalny", "password": "haslo-redaktora-12345"})
    assert refused.status_code == 200 and not logged_in(client)
    assert "Edytuj w django CMS" in refused.content.decode()

    accepted = client.post(login, {"username": "admin@example.com", "password": "haslo-admina-123456"})
    assert accepted.status_code == 302 and logged_in(client)


# --- wspólny wektor z aplikacją główną -------------------------------------------------------------


def _contract_cases() -> dict:
    return json.loads(
        (Path(settings.DJCMS_CONTRACT_DIR) / "sso_token_cases.json").read_text(encoding="utf-8")
    )


def test_contract_vector(settings):
    """Token wystawiony przez ``backend/apps/cms/djcms_sso.py`` przechodzi weryfikację tutaj – co do pola."""
    contract = _contract_cases()
    settings.DJCMS_SSO_KEY = contract["key"]
    for case in contract["cases"]:
        claims = sso.verify_token(case["token"], host=case["host"], now=contract["now"] + 1)
        assert claims.sub == case["user"]["pk"]
        assert claims.email == case["user"]["email"]
        assert claims.platform is case["platform"]
        assert {slug: sorted(abilities) for slug, abilities in claims.competitions.items()} == {
            slug: sorted(abilities) for slug, abilities in case["competitions"] if "edit" in abilities
        }
        assert claims.nonce == case["nonce"]
        with pytest.raises(sso.SsoError):
            sso.verify_token(case["token"], host="inny.example", now=contract["now"] + 1)
        with pytest.raises(sso.SsoError):
            sso.verify_token(case["token"], host=case["host"], now=contract["now"] + 61)
    assert SSO_KEY != contract["key"]


# --- system checki (DJ-02g) ---------------------------------------------------------------------


def test_e001_requires_cms_permission(settings):
    from apps.sites.checks import check_cms_permissions_enabled

    assert check_cms_permissions_enabled() == []
    settings.CMS_PERMISSION = False
    assert [error.id for error in check_cms_permissions_enabled()] == ["dj_sites.E001"]


def test_w001_flags_short_and_shared_sso_keys(settings):
    from apps.sites.checks import check_sso_key

    settings.DJCMS_SSO_KEY = ""
    assert check_sso_key() == []  # pusty klucz = SSO wyłączone, stan poprawny
    settings.DJCMS_SSO_KEY = SSO_KEY
    assert check_sso_key() == []
    settings.DJCMS_SSO_KEY = "k" * 31
    assert [warning.id for warning in check_sso_key()] == ["dj_sites.W001"]
    settings.DJCMS_SSO_KEY = settings.DJCMS_INTERNAL_TOKEN
    assert [warning.id for warning in check_sso_key()] == ["dj_sites.W001"]
    settings.DJCMS_SSO_KEY = settings.SECRET_KEY
    assert [warning.id for warning in check_sso_key()] == ["dj_sites.W001"]
