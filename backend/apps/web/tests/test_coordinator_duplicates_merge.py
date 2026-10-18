"""Scalanie zdublowanych kont na ekranie koordynatora (ACC-MERGE-01).

Reguły scalania sprawdza ``apps/accounts/tests/test_merge.py``; tutaj – warstwa WWW: uprawnienia,
404 na koncie cudzego konkursu, formularz „Scal…” przy grupie, podgląd (z konfliktem – bez
przycisku), potwierdzenie niesione przez sam przycisk, scalenie z powrotem na listę i opcja adresu.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole, User
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.models import StageEntry
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/accounts/duplicates/"
URL = "/coordinator/accounts/duplicates/merge/"


def person(competition, *, email, logged_in=False, **user):
    return ParticipantFactory(
        competition=competition,
        user=UserFactory(
            email=email,
            first_name="Anna",
            last_name="Nowak",
            last_login=timezone.now() if logged_in else None,
            **user,
        ),
        school="LO nr 5 w Bielsku-Białej",
    )


@pytest.fixture
def pair(competition):
    keep = person(competition, email="anna.nowak@gmail.com", logged_in=True)
    drop = person(competition, email="anna.nowak@gmail.con")
    return keep, drop


@pytest.fixture
def coordinator_client(web_client, coordinator):
    web_client.force_login(coordinator)
    return web_client


def query(keep, drop) -> dict:
    return {"target": keep.user_id, "source": drop.user_id}


def test_only_the_coordinator_may_merge(web_client, pair):
    keep, drop = pair
    web_client.force_login(keep.user)

    assert web_client.get(URL, query(keep, drop)).status_code == 403
    assert web_client.post(URL, {**query(keep, drop), "confirm": drop.user_id}).status_code == 403
    assert User.objects.filter(pk=drop.user_id).exists()


def test_an_account_from_another_competition_is_not_found(client_for, competition, other_competition, pair):
    keep, _drop = pair
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(coordinator)
    stranger = ParticipantFactory(competition=other_competition)

    response = client.post(
        URL, {"target": keep.user_id, "source": stranger.user_id, "confirm": stranger.user_id}
    )

    assert response.status_code == 404
    assert User.objects.filter(pk=stranger.user_id).exists()


def test_garbage_ids_are_a_bad_request(coordinator_client, pair):
    keep, _drop = pair

    assert coordinator_client.get(URL, {"target": "x", "source": "1"}).status_code == 400
    assert coordinator_client.get(URL, {"target": keep.user_id, "source": keep.user_id}).status_code == 400


def test_the_list_offers_merge_with_the_account_to_keep_preselected(coordinator_client, pair):
    keep, drop = pair

    body = coordinator_client.get(LIST_URL).content.decode()

    assert f'action="{URL}"' in body
    assert "Scal…" in body
    assert f'<option value="{keep.user_id}" selected>' in body
    assert f'<option value="{drop.user_id}" selected>' in body


def test_the_preview_shows_what_moves_and_the_confirm_button(coordinator_client, competition, pair):
    keep, drop = pair
    StageEntryFactory(competition=competition, participant=drop)

    response = coordinator_client.get(URL, query(keep, drop))
    body = response.content.decode()

    assert response.status_code == 200
    assert "wpisy do etapów" in body
    assert f"Tak, scal konto {drop.public_code} w {keep.public_code}" in body
    assert f'name="confirm" value="{drop.user_id}"' in body
    # Konto docelowe potwierdziło adres – opcji wzięcia adresu kopii nie ma.
    assert 'name="take_email"' not in body
    # Podgląd niczego nie zmienia.
    assert StageEntry.objects.get(participant=drop).participant_id == drop.pk


def test_a_conflict_hides_the_confirm_button(coordinator_client, competition, pair):
    keep, drop = pair
    stage = StageFactory(competition=competition, name="Etap I")
    for participant in pair:
        SubmissionFactory(
            competition=competition,
            entry=StageEntryFactory(competition=competition, participant=participant, stage=stage),
        )

    body = coordinator_client.get(URL, query(keep, drop)).content.decode()

    assert "Scalenie niemożliwe" in body
    assert "Etap I" in body
    assert "Tak, scal konto" not in body


def test_post_without_the_confirm_button_merges_nothing(coordinator_client, pair):
    keep, drop = pair

    response = coordinator_client.post(URL, query(keep, drop))

    assert response.status_code == 302
    assert response["Location"].startswith(URL)
    assert User.objects.filter(pk=drop.user_id).exists()


def test_post_merges_and_returns_to_the_list(coordinator_client, competition, pair):
    keep, drop = pair
    entry = StageEntryFactory(competition=competition, participant=drop)

    response = coordinator_client.post(URL, {**query(keep, drop), "confirm": drop.user_id}, follow=True)

    assert response.redirect_chain[-1][0] == LIST_URL
    assert f"Scalono konto {drop.public_code} w {keep.public_code}" in response.content.decode()
    assert StageEntry.objects.get(pk=entry.pk).participant_id == keep.pk
    assert not User.objects.filter(pk=drop.user_id).exists()
    assert AuditLog.objects.filter(action="account.merged", target_id=str(keep.user_id)).exists()


def test_the_target_without_a_confirmed_address_may_take_the_address_of_the_copy(
    coordinator_client, competition
):
    keep = person(competition, email="anna.nowak@gmail.con", email_verified_at=None, is_active=False)
    drop = person(competition, email="anna.nowak@gmail.com", logged_in=True)

    preview = coordinator_client.get(URL, query(keep, drop)).content.decode()
    assert 'name="take_email"' in preview

    coordinator_client.post(URL, {**query(keep, drop), "confirm": drop.user_id, "take_email": "1"})

    assert User.objects.get(pk=keep.user_id).email == "anna.nowak@gmail.com"
    assert not User.objects.filter(pk=drop.user_id).exists()


def test_accounts_outside_one_duplicate_group_are_not_merged(coordinator_client, competition, pair):
    keep, _drop = pair
    other = ParticipantFactory(competition=competition, school="Inna szkoła")

    response = coordinator_client.post(
        URL, {"target": keep.user_id, "source": other.user_id, "confirm": other.user_id}
    )

    assert response.status_code == 302
    assert User.objects.filter(pk=other.user_id).exists()
