"""Ekran „Nieaktywne konta” w panelu koordynatora (ACC-DUP-02).

Filtr, zakres i warunki usuwania sprawdza ``apps/accounts/tests/test_inactive.py``; tutaj – warstwa
WWW: uprawnienia, liczniki i filtry w adresie, „zaznacz wszystkie”, „Usuń” w wierszu z potwierdzeniem
i bez, powrót z filtrami, zbiorcze dwa kroki z pominięciem konta, które zdążyło się zalogować, 404 na
koncie cudzego konkursu, eksport z audytem i koszt strony niezależny od liczby wierszy.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.account_cleanup import login_stamp
from apps.accounts.models import CompetitionRole, User
from apps.accounts.tests.factories import CommitteeMemberFactory, CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/coordinator/accounts/inactive/"
BULK_URL = "/coordinator/accounts/inactive/delete/"
EXPORT_URL = "/coordinator/accounts/inactive/export.csv"
FILTER = {"login": "never", "joined": "7"}

#: Bezpiecznik „rzędu wielkości” obok asercji o niezmienności kosztu (jak w ekranie duplikatów).
PAGE_QUERY_CEILING = 45


def one_url(user) -> str:
    return f"/coordinator/accounts/inactive/{user.pk}/delete/"


def member(competition, *, last_login=None, joined_days_ago=30, **user):
    fields = {f"user__{key}": value for key, value in user.items()}
    return ParticipantFactory(
        competition=competition,
        user__date_joined=timezone.now() - timedelta(days=joined_days_ago),
        user__last_login=last_login,
        **fields,
    )


@pytest.fixture
def coordinator_client(web_client, coordinator):
    web_client.force_login(coordinator)
    return web_client


# --- uprawnienia ---------------------------------------------------------------------------------


def test_only_the_coordinator_sees_the_screen(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(URL).status_code == 403
    assert web_client.post(BULK_URL, {"account": [participant.user.pk], **FILTER}).status_code == 403
    assert web_client.post(one_url(participant.user), {"confirm": participant.user.pk}).status_code == 403
    assert web_client.get(EXPORT_URL).status_code == 403
    assert User.objects.filter(pk=participant.user.pk).exists()


def test_accounts_of_another_competition_are_not_found(client_for, competition, other_competition):
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(coordinator)
    mine = member(competition)
    stranger = member(other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    bulk = client.post(BULK_URL, {"account": [mine.user.pk, stranger.user.pk], "confirm": "1", **FILTER})
    single = client.post(one_url(stranger.user), {"confirm": stranger.user.pk, "seen_login": "", **FILTER})

    assert bulk.status_code == 404
    assert single.status_code == 404
    assert User.objects.filter(pk__in=[mine.user.pk, stranger.user.pk]).count() == 2


def test_garbage_ids_and_bad_filters_are_a_bad_request(coordinator_client, competition):
    profile = member(competition)

    assert coordinator_client.post(BULK_URL, {"account": ["abc"], **FILTER}).status_code == 400
    assert (
        coordinator_client.post(BULK_URL, {"account": [profile.user.pk], "login": "days"}).status_code == 400
    )
    too_many = {"account": list(range(1, 502)), **FILTER}
    assert coordinator_client.post(BULK_URL, too_many).status_code == 400
    assert coordinator_client.get(EXPORT_URL, {"login": "days", "days": "0"}).status_code == 400


# --- lista ---------------------------------------------------------------------------------------


def test_the_default_screen_lists_never_logged_in_accounts_older_than_a_week(coordinator_client, competition):
    never = member(competition)
    fresh = member(competition, joined_days_ago=1)
    active = member(competition, last_login=timezone.now())

    response = coordinator_client.get(URL)
    body = response.content.decode()

    assert response.status_code == 200
    assert [account.participant.pk for account in response.context["rows"]] == [never.pk]
    assert response.context["counts"]["total"] == 1
    assert never.user.email in body
    assert fresh.user.email not in body
    assert active.user.email not in body
    assert "Nieaktywne konta" in body  # pozycja menu i tytuł
    assert f'action="{one_url(never.user)}"' in body
    assert 'name="confirm"' in body
    assert 'form="inactive-bulk"' in body


def test_days_filter_from_the_address(coordinator_client, competition):
    stale = member(competition, last_login=timezone.now() - timedelta(days=100))
    recent = member(competition, last_login=timezone.now() - timedelta(days=10))

    response = coordinator_client.get(URL, {"login": "days", "days": "30", "joined": "0"})

    pks = [account.participant.pk for account in response.context["rows"]]
    assert stale.pk in pks
    assert recent.pk not in pks
    assert "login=days&amp;days=30&amp;joined=0" in response.content.decode()


def test_a_bad_filter_shows_an_error_and_no_list(coordinator_client, competition):
    member(competition)

    response = coordinator_client.get(URL, {"login": "days", "days": "5000"})
    body = response.content.decode()

    assert response.status_code == 200
    assert "Popraw filtr" in body
    assert "rows" not in response.context
    assert 'value="5000"' in body


def test_select_all_checks_every_box_on_the_page(coordinator_client, competition):
    member(competition)
    member(competition)

    def table(params):
        body = coordinator_client.get(URL, params).content.decode()
        return body.split("<tbody>")[1].split("</tbody>")[0]

    plain = table(FILTER)
    selected = table({**FILTER, "zaznacz": "1"})

    assert plain.count('form="inactive-bulk"') == 2
    assert " checked" not in plain
    assert selected.count(" checked") == 2


def test_an_account_with_other_roles_has_no_bulk_checkbox_but_a_single_delete(
    coordinator_client, competition
):
    reviewer = member(competition)
    CommitteeMemberFactory(competition=competition, user=reviewer.user)

    body = coordinator_client.get(URL).content.decode()

    assert "inne role: komitet" in body
    assert f'value="{reviewer.user.pk}" form="inactive-bulk"' not in body
    assert f'action="{one_url(reviewer.user)}"' in body


# --- usunięcie w wierszu -------------------------------------------------------------------------


def test_single_delete_with_confirmation_returns_to_the_same_filtered_page(coordinator_client, competition):
    profile = member(competition, last_login=timezone.now() - timedelta(days=100))
    data = {
        "confirm": profile.user.pk,
        "seen_login": login_stamp(profile.user),
        "login": "days",
        "days": "90",
        "joined": "7",
        "page": "1",
    }

    response = coordinator_client.post(one_url(profile.user), data)

    assert response.status_code == 302
    assert response.url == f"{URL}?login=days&days=90&joined=7&page=1"
    assert not User.objects.filter(pk=profile.user.pk).exists()
    assert AuditLog.objects.filter(
        action="account.deleted_by_coordinator", target_id=str(profile.user.pk)
    ).exists()


def test_single_delete_without_confirmation_is_refused(coordinator_client, competition):
    profile = member(competition)

    for data in ({}, {"confirm": "tak"}, {"confirm": str(profile.user.pk + 1)}):
        response = coordinator_client.post(
            one_url(profile.user), {**FILTER, "seen_login": "", **data}, follow=True
        )
        assert "wymaga potwierdzenia" in response.content.decode()

    assert User.objects.filter(pk=profile.user.pk).exists()
    assert not AuditLog.objects.filter(action="account.deleted_by_coordinator").exists()


def test_single_delete_skips_an_account_that_logged_in_after_the_list_was_shown(
    coordinator_client, competition
):
    profile = member(competition)
    User.objects.filter(pk=profile.user.pk).update(last_login=timezone.now())

    response = coordinator_client.post(
        one_url(profile.user), {"confirm": profile.user.pk, "seen_login": "", **FILTER}, follow=True
    )

    assert "nie zostało usunięte" in response.content.decode()
    assert User.objects.filter(pk=profile.user.pk).exists()


def test_the_coordinator_never_deletes_himself(coordinator_client, coordinator, competition):
    ParticipantFactory(competition=competition, user=coordinator)

    coordinator_client.post(one_url(coordinator), {"confirm": coordinator.pk, "seen_login": "", **FILTER})

    assert User.objects.filter(pk=coordinator.pk).exists()


# --- zbiorcze usunięcie --------------------------------------------------------------------------


def test_bulk_delete_confirms_the_exact_list_then_skips_an_account_that_logged_in(
    coordinator_client, competition
):
    first, second = member(competition), member(competition)
    ids = [first.user.pk, second.user.pk]

    confirm = coordinator_client.post(BULK_URL, {"account": ids, **FILTER})
    body = confirm.content.decode()
    assert confirm.status_code == 200
    assert first.user.email in body
    assert second.user.email in body
    assert 'name="confirm"' in body
    assert 'name="login" value="never"' in body
    assert User.objects.filter(pk__in=ids).count() == 2  # pierwszy krok niczego nie usuwa

    User.objects.filter(pk=second.user.pk).update(last_login=timezone.now())
    response = coordinator_client.post(BULK_URL, {"account": ids, "confirm": "1", **FILTER}, follow=True)
    body = response.content.decode()

    assert not User.objects.filter(pk=first.user.pk).exists()
    assert User.objects.filter(pk=second.user.pk).exists()
    assert "Usunięto kont: 1." in body
    assert "Pominięto kont: 1" in body
    assert AuditLog.objects.filter(action="account.deleted_by_coordinator").count() == 1


def test_bulk_confirmation_lists_skipped_accounts_with_a_reason(coordinator_client, competition):
    active = member(competition, last_login=timezone.now())
    reviewer = member(competition)
    CommitteeMemberFactory(competition=competition, user=reviewer.user)

    body = coordinator_client.post(
        BULK_URL, {"account": [active.user.pk, reviewer.user.pk], **FILTER}
    ).content.decode()

    assert "Zostaną pominięte" in body
    assert "nie spełnia już filtra" in body
    assert "inne role" in body
    assert "Żadne z wybranych kont" in body


# --- eksport i koszt -----------------------------------------------------------------------------


def test_export_uses_the_filter_and_is_audited(coordinator_client, competition):
    never = member(competition)
    active = member(competition, last_login=timezone.now())

    response = coordinator_client.get(EXPORT_URL, FILTER)
    content = b"".join(response.streaming_content).decode("utf-8")

    assert response["Content-Type"].startswith("text/csv")
    assert never.user.email in content
    assert active.user.email not in content
    entry = AuditLog.objects.get(action="account.inactive_exported")
    assert entry.diff["rows"] == 1
    assert entry.diff["login_days"] is None
    assert entry.diff["joined_days"] == 7


def test_page_query_count_does_not_grow_with_rows(
    coordinator_client, competition, django_assert_max_num_queries
):
    member(competition)
    coordinator_client.get(URL)  # rozgrzewka pamięci podręcznych procesu

    with django_assert_max_num_queries(PAGE_QUERY_CEILING) as few:
        assert coordinator_client.get(URL).status_code == 200

    for _ in range(6):
        member(competition)
    with django_assert_max_num_queries(PAGE_QUERY_CEILING) as many:
        assert coordinator_client.get(URL).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)
