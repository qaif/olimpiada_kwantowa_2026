"""Kryterium 5 z T-09: kto wchodzi do ``/cms/``.

Uprawnienia nadaje migracja ``apps.cms.0003_coordinator_permissions`` (grupa ``coordinator``
dostaje komplet praw grup ``Editors`` i ``Moderators`` Wagtaila). Test sprawdza skutek, a nie
listę kodowych nazw – ta zależy od wersji Wagtaila.
"""

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import GROUP_COORDINATOR
from apps.accounts.tests.factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db


def login(client, user) -> None:
    assert client.login(email=user.email, password=DEFAULT_PASSWORD)


def test_anonymous_is_redirected_to_wagtail_login(web_client):
    response = web_client.get("/cms/")

    assert response.status_code == 302
    assert "/cms/login/" in response.headers["Location"]


def test_participant_has_no_access_to_cms(web_client, participant):
    login(web_client, participant.user)

    response = web_client.get("/cms/")

    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert "/cms/" in response.headers["Location"]


def test_reviewer_has_no_access_to_cms(web_client, reviewer):
    login(web_client, reviewer.user)

    assert web_client.get("/cms/").status_code in (302, 403)


def test_coordinator_can_open_cms(web_client, coordinator):
    login(web_client, coordinator)

    response = web_client.get("/cms/", follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == []


def test_coordinator_group_has_wagtail_permissions():
    group = Group.objects.get(name=GROUP_COORDINATOR)

    codenames = set(group.permissions.values_list("codename", flat=True))
    assert "access_admin" in codenames
    assert {"add_image", "add_document"} <= codenames
    # Editors + Moderators: dodawanie i edycja to za mało, musi być też publikacja.
    page_codenames = set(group.page_permissions.values_list("permission__codename", flat=True))
    assert {"add_page", "change_page", "publish_page"} <= page_codenames
    assert group.collection_permissions.exists()


def test_web_urls_still_win_over_wagtail_catch_all(web_client, coordinator):
    """Kolejność z ``config/urls.py``: aplikacja przed catch-all CMS-a."""
    login(web_client, coordinator)

    assert web_client.get("/coordinator/").status_code == 200
    assert web_client.get("/healthz/").status_code == 200
    assert web_client.get("/api/schema/").status_code == 200
    # Adres, którego nie zna ani apps.web, ani drzewo stron.
    assert web_client.get("/nie-ma-takiej-strony/").status_code == 404
