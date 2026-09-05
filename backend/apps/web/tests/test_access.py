"""Kryteria 1–2 z T-08: niezalogowany dostaje 302 na logowanie, zła rola dostaje 403."""

import pytest

pytestmark = pytest.mark.django_db

PROTECTED = ["/me/", "/review/", "/coordinator/", "/appeals/"]


@pytest.mark.parametrize("url", PROTECTED)
def test_anonymous_is_redirected_to_login(web_client, url):
    response = web_client.get(url)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/login/?next={url}"


@pytest.mark.parametrize("url", ["/review/", "/coordinator/", "/appeals/"])
def test_participant_gets_403_outside_own_panel(web_client, participant, url):
    web_client.force_login(participant.user)
    assert web_client.get(url).status_code == 403


@pytest.mark.parametrize("url", ["/coordinator/", "/appeals/", "/me/"])
def test_reviewer_gets_403_outside_review_panel(web_client, reviewer, url):
    web_client.force_login(reviewer.user)
    assert web_client.get(url).status_code == 403


def test_reviewer_sees_own_panel(web_client, reviewer):
    web_client.force_login(reviewer.user)
    assert web_client.get("/review/").status_code == 200


def test_coordinator_sees_own_panel(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)
    assert web_client.get("/coordinator/").status_code == 200


def test_home_is_public(web_client, elim_stage):
    assert web_client.get("/").status_code == 200


@pytest.mark.parametrize(
    "role,expected",
    [("participant", "/me/"), ("reviewer", "/review/"), ("coordinator", "/coordinator/")],
)
def test_login_lands_on_panel_matching_role(web_client, participant, reviewer, coordinator, role, expected):
    """Bez ``next`` logowanie prowadzi do panelu roli – recenzent nie ląduje na 403 uczestnika."""
    from apps.accounts.tests.factories import DEFAULT_PASSWORD

    user = {"participant": participant.user, "reviewer": reviewer.user, "coordinator": coordinator}[role]

    response = web_client.post("/login/", {"username": user.email, "password": DEFAULT_PASSWORD})

    assert response.status_code == 302
    assert response.headers["Location"] == expected


def test_login_honours_next_parameter(web_client, reviewer):
    from apps.accounts.tests.factories import DEFAULT_PASSWORD

    response = web_client.post(
        "/login/?next=/review/", {"username": reviewer.user.email, "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/review/"
