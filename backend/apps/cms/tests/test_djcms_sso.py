"""Przejście redaktora z ``/cms/`` do django CMS (SSO, DJ-02 D6, S11) – strona aplikacji głównej.

Zakres (kto co redaguje – lustro ``/cms/``), kształt i podpis tokenu, formularz ``POST`` z CSRF,
brak tokenu w adresie i w logach, odmowy, pozycja menu, ``cms.W013`` i wspólny wektor z djcms.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import Group, Permission
from django.test import Client
from wagtail.models import GroupPagePermission

from apps.accounts.tests.factories import UserFactory
from apps.cms import djcms_sso
from apps.cms.checks import check_djcms_sso_key
from apps.cms.permissions import ensure_cms_group
from apps.tenancy.models import Competition, RoutingMode
from conftest import allow_test_hosts

pytestmark = pytest.mark.django_db

KEY = "s" * 40
URL = "/cms/django-cms/"


@pytest.fixture(autouse=True)
def _sso_key(settings):
    settings.DJCMS_SSO_KEY = KEY


def editor_of(*competitions, **user_kwargs):
    """Konto z grupą ``cms:<slug>`` (strony i publikacja w poddrzewie konkursu, dostęp do ``/cms/``)."""
    user = UserFactory(**user_kwargs)
    for competition in competitions:
        user.groups.add(ensure_cms_group(competition))
    return user


def edit_only_group(competition) -> Group:
    """Grupa na wzór ``Editors``: edycja bez publikacji na korzeniu witryny konkursu."""
    group = Group.objects.create(name=f"redakcja-bez-publikacji-{competition.slug}")
    group.permissions.add(Permission.objects.get(codename="access_admin"))
    for codename in ("add_page", "change_page"):
        GroupPagePermission.objects.create(
            group=group,
            page_id=competition.site.root_page_id,
            permission=Permission.objects.get(content_type__app_label="wagtailcore", codename=codename),
        )
    return group


def decode(token: str, key: str = KEY) -> dict:
    prefix, body, signature = token.split(".")
    assert prefix == "v1"
    expected = hmac.new(key.encode(), b"olimpiada/djcms-sso/v1." + body.encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def token_from(response) -> str:
    match = re.search(r'name="token" value="([^"]+)"', response.content.decode())
    assert match, response.content.decode()[:500]
    return match.group(1)


# --- zakres -------------------------------------------------------------------------------------


def test_competition_editor_gets_only_their_competition(competition, other_competition):
    grants = djcms_sso.editor_grants(editor_of(competition))
    assert grants.platform is False
    assert grants.competitions == ((competition.slug, ("edit", "publish")),)
    assert grants.allows(competition.slug)
    assert not grants.allows(other_competition.slug)


def test_edit_only_account_carries_no_publish(competition):
    user = UserFactory()
    user.groups.add(edit_only_group(competition))
    assert djcms_sso.editor_grants(user).competitions == ((competition.slug, ("edit",)),)


def test_superuser_maps_to_platform(competition, other_competition):
    grants = djcms_sso.editor_grants(UserFactory(is_superuser=True, is_staff=True))
    assert grants.platform is True
    assert {slug for slug, _ in grants.competitions} == {competition.slug, other_competition.slug}


def test_account_without_cms_access_gets_nothing(competition):
    assert djcms_sso.editor_grants(UserFactory()).empty
    inactive = editor_of(competition, is_active=False)
    assert djcms_sso.editor_grants(inactive).empty


def test_inactive_competition_is_not_granted(competition, other_competition):
    user = editor_of(competition, other_competition)
    Competition.objects.filter(pk=other_competition.pk).update(is_active=False)
    assert djcms_sso.editor_grants(user).competitions == ((competition.slug, ("edit", "publish")),)


def test_editing_freeze_does_not_hide_the_editor(competition):
    from apps.cms import freeze

    freeze.set_frozen(True)
    try:
        assert djcms_sso.editor_grants(editor_of(competition)).allows(competition.slug)
    finally:
        freeze.set_frozen(False)


def test_known_hosts_are_competition_hosts(competition, other_competition, settings):
    settings.DEBUG = False
    assert djcms_sso.known_host(competition.primary_domain)
    assert djcms_sso.known_host(other_competition.primary_domain)
    assert not djcms_sso.known_host("evil.example")


# --- token --------------------------------------------------------------------------------------


def test_token_shape_and_signature(competition):
    user = editor_of(competition, first_name="Ala", last_name="Nowak")
    now = int(time.time())
    token = djcms_sso.issue_token(
        user=user, host="kwantowa.invalid", grants=djcms_sso.editor_grants(user), now=now
    )

    payload = decode(token)

    assert payload == {
        "v": 1,
        "aud": "djcms",
        "iss": "web",
        "sub": user.pk,
        "email": user.email,
        "first_name": "Ala",
        "last_name": "Nowak",
        "host": "kwantowa.invalid",
        "platform": False,
        "competitions": [{"slug": competition.slug, "abilities": ["edit", "publish"]}],
        "nonce": payload["nonce"],
        "iat": now,
        "exp": now + 60,
    }
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", payload["nonce"])
    other = djcms_sso.issue_token(
        user=user, host="kwantowa.invalid", grants=djcms_sso.editor_grants(user), now=now
    )
    assert decode(other)["nonce"] != payload["nonce"]


def test_no_token_without_a_key(competition, settings):
    settings.DJCMS_SSO_KEY = "k" * 31
    with pytest.raises(RuntimeError):
        djcms_sso.issue_token(user=UserFactory(), host="x", grants=djcms_sso.EditorGrants(False, ()))


def test_contract_vector_matches_issue_token(settings):
    """Wspólny wektor z djcms (``djcms/apps/sites/tests/test_sso.py``) – ten sam token co do bajtu."""
    path = Path(django_settings.BASE_DIR) / "djcms_contract" / "sso_token_cases.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    settings.DJCMS_SSO_KEY = contract["key"]
    for case in contract["cases"]:
        grants = djcms_sso.EditorGrants(
            platform=case["platform"], competitions=tuple((s, tuple(a)) for s, a in case["competitions"])
        )
        token = djcms_sso.issue_token(
            user=SimpleNamespace(**case["user"]),
            host=case["host"],
            grants=grants,
            now=contract["now"],
            nonce=case["nonce"],
        )
        assert token == case["token"], case["name"]


# --- widok -----------------------------------------------------------------------------------------


def test_get_shows_a_button_and_no_token(competition, client_for):
    client = client_for(competition)
    client.force_login(editor_of(competition))

    response = client.get(URL)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-djcms-handoff="confirm"' in html
    assert 'name="token"' not in html
    assert "csrfmiddlewaretoken" in html
    assert "no-store" in response["Cache-Control"]


def test_post_renders_the_auto_submit_form_to_the_same_host(competition, client_for, caplog):
    client = client_for(competition)
    user = editor_of(competition)
    client.force_login(user)

    with caplog.at_level(logging.INFO, logger="apps.cms.djcms_sso"):
        response = client.post(URL)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'action="/djcms/sso/"' in html
    assert 'method="post"' in html
    assert "no-store" in response["Cache-Control"]
    token = token_from(response)
    payload = decode(token)
    assert payload["host"] == competition.primary_domain
    assert payload["sub"] == user.pk
    assert token not in caplog.text
    assert f"konto #{user.pk}" in caplog.text


def test_post_requires_csrf(competition, settings):
    allow_test_hosts(settings)
    client = Client(enforce_csrf_checks=True, HTTP_HOST=competition.primary_domain)
    client.force_login(editor_of(competition))
    response = client.post(URL)
    assert response.status_code == 403
    assert 'name="token"' not in response.content.decode()


def test_editor_of_another_competition_is_refused(competition, other_competition, client_for):
    client = client_for(competition)
    client.force_login(editor_of(other_competition))
    for response in (client.get(URL), client.post(URL)):
        assert response.status_code in (302, 403)
        assert 'name="token"' not in response.content.decode()


def test_without_key_the_view_explains_and_issues_nothing(competition, client_for, settings):
    settings.DJCMS_SSO_KEY = ""
    client = client_for(competition)
    client.force_login(editor_of(competition))
    response = client.post(URL)
    assert response.status_code == 503
    assert 'data-djcms-handoff="disabled"' in response.content.decode()


def test_anonymous_goes_to_the_login(competition, client_for):
    response = client_for(competition).post(URL)
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_path_prefix_competition_posts_under_its_prefix(competition, other_competition, client_for):
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    Competition.objects.filter(pk=other_competition.pk).update(
        routing_mode=RoutingMode.PATH, path_prefix="druga"
    )
    client = client_for(competition)
    client.force_login(editor_of(other_competition))

    response = client.post(f"/druga{URL}")

    assert response.status_code == 200
    assert 'action="/druga/djcms/sso/"' in response.content.decode()
    payload = decode(token_from(response))
    assert payload["host"] == competition.primary_domain
    assert payload["competitions"] == [{"slug": other_competition.slug, "abilities": ["edit", "publish"]}]


def test_menu_item_only_for_editors_of_this_competition(competition, other_competition, client_for, settings):
    client = client_for(competition)
    client.force_login(editor_of(competition, other_competition))
    assert "Edytuj w django CMS" in client.get("/cms/").content.decode()

    settings.DJCMS_SSO_KEY = ""
    assert "Edytuj w django CMS" not in client.get("/cms/").content.decode()

    settings.DJCMS_SSO_KEY = KEY
    other = client_for(competition)
    other.force_login(editor_of(other_competition))
    html = other.get("/cms/").content.decode()
    assert "Edytuj w django CMS" not in html


# --- cms.W013 -----------------------------------------------------------------------------------


def test_w013_flags_short_and_shared_keys(settings):
    settings.DJCMS_SSO_KEY = ""
    assert check_djcms_sso_key() == []
    settings.DJCMS_SSO_KEY = KEY
    settings.DJCMS_INTERNAL_TOKEN = "t" * 40
    assert check_djcms_sso_key() == []

    settings.DJCMS_SSO_KEY = "krotki"
    assert [m.id for m in check_djcms_sso_key()] == ["cms.W013"]
    settings.DJCMS_SSO_KEY = settings.DJCMS_INTERNAL_TOKEN
    assert [m.id for m in check_djcms_sso_key()] == ["cms.W013"]
    settings.DJCMS_SSO_KEY = settings.SECRET_KEY
    assert "SECRET_KEY" in check_djcms_sso_key()[-1].msg
