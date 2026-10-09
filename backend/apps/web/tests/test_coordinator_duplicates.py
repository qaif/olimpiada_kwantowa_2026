"""Ekran zdublowanych kont w panelu koordynatora (ACC-DUP-01, ACC-DUP-02).

Reguły grupowania i sugestii sprawdza ``apps/accounts/tests/test_duplicates.py``; tutaj – to, co
dokłada warstwa WWW: uprawnienia, przyciski przy właściwych kontach, „Usuń” w wierszu
(z potwierdzeniem i bez, ze znacznikiem logowania, ostatnie konto osoby), powrót z istniejącego
ekranu usuwania, dwa kroki zbiorczego usunięcia (z pominięciem konta, które zdążyło się zalogować),
404 na koncie cudzego konkursu, eksport z audytem i koszt strony niezależny od liczby grup.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.account_cleanup import login_stamp
from apps.accounts.models import CompetitionRole, User
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/coordinator/accounts/duplicates/"
DELETE_URL = "/coordinator/accounts/duplicates/delete/"
EXPORT_URL = "/coordinator/accounts/duplicates/export.csv"

#: Bezpiecznik „rzędu wielkości” obok asercji o niezmienności kosztu (jak w
#: ``test_coordinator_member_card.py``): stały narzut panelu (sesja, menu, liczniki) plus pięć
#: zapytań serwisu. Lokalnie, a nie w ``apps/core/tests/query_budgets.py``, żeby nie dokładać
#: wiersza do pliku, który zmienia równolegle kilka gałęzi.
PAGE_QUERY_CEILING = 45


def person(competition, *, first="Anna", last="Nowak", logged_in=False, email=None):
    fields = {"user__email": email} if email else {}
    return ParticipantFactory(
        competition=competition,
        user__first_name=first,
        user__last_name=last,
        user__last_login=timezone.now() if logged_in else None,
        school="LO nr 5 w Bielsku-Białej",
        **fields,
    )


@pytest.fixture
def trio(competition):
    """Osoba z jednym kontem używanym i dwiema kopiami, które nigdy się nie logowały."""
    return {
        "keeper": person(competition, logged_in=True, email="anna.nowak@gmail.com"),
        "copy": person(competition, email="anna.nowak@gmail.con"),
        "late": person(competition, email="anna.nowak@lo5.bielsko.pl"),
    }


@pytest.fixture
def coordinator_client(web_client, coordinator):
    web_client.force_login(coordinator)
    return web_client


# --- uprawnienia ---------------------------------------------------------------------------------


def test_only_the_coordinator_sees_the_screen(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(URL).status_code == 403
    assert web_client.post(DELETE_URL, {"account": [participant.user.pk]}).status_code == 403
    one = f"/coordinator/accounts/duplicates/{participant.user.pk}/delete/"
    assert web_client.post(one, {"confirm": participant.user.pk}).status_code == 403
    assert web_client.get(EXPORT_URL).status_code == 403
    assert User.objects.filter(pk=participant.user.pk).exists()


def test_bulk_delete_of_an_account_from_another_competition_is_not_found(
    client_for, competition, other_competition, trio
):
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(coordinator)
    stranger = ParticipantFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    response = client.post(DELETE_URL, {"account": [trio["copy"].user.pk, stranger.user.pk], "confirm": "1"})

    assert response.status_code == 404
    assert User.objects.filter(pk__in=[trio["copy"].user.pk, stranger.user.pk]).count() == 2


def test_garbage_ids_are_a_bad_request(coordinator_client):
    assert coordinator_client.post(DELETE_URL, {"account": ["abc"]}).status_code == 400


# --- lista ---------------------------------------------------------------------------------------


def one_url(user) -> str:
    return f"/coordinator/accounts/duplicates/{user.pk}/delete/"


def test_the_screen_shows_counters_groups_and_inline_delete_at_every_account(coordinator_client, trio):
    response = coordinator_client.get(URL)
    body = response.content.decode()

    assert response.status_code == 200
    report = response.context["report"]
    assert (report.people, report.accounts, len(response.context["candidates"])) == (1, 3, 2)
    for profile in trio.values():
        assert profile.user.email in body
        assert profile.public_code in body
        # ACC-DUP-02: „Usuń” w wierszu przy każdym koncie, które serwis pozwala usunąć.
        assert f'action="{one_url(profile.user)}"' in body
        assert f"Tak, usuń konto {profile.public_code}" in body
    assert 'id="grupa-1"' in body
    # Konto używane ma w potwierdzeniu wyraźne ostrzeżenie.
    assert "To jedyne używane konto tej osoby" in body
    assert "Konto logowało się" in body
    assert "do zachowania" in body
    assert "kandydat do usunięcia" in body
    assert "Usuń wszystkich kandydatów (2)" in body
    assert "Zdublowane konta" in body  # pozycja menu i tytuł


def test_a_protected_account_has_no_inline_delete(coordinator_client, coordinator, competition):
    ParticipantFactory(competition=competition, user=coordinator, school="LO nr 5 w Bielsku-Białej")
    coordinator.first_name, coordinator.last_name = "Anna", "Nowak"
    coordinator.save(update_fields=["first_name", "last_name"])
    other = person(competition, logged_in=True)

    body = coordinator_client.get(URL).content.decode()

    assert f'action="{one_url(coordinator)}"' not in body
    assert f'action="{one_url(other.user)}"' in body


def test_the_empty_screen_says_so(coordinator_client):
    body = coordinator_client.get(URL).content.decode()

    assert "Nie ma zdublowanych kont" in body
    assert "Usuń wszystkich kandydatów" not in body


def test_single_delete_goes_through_the_existing_screen_and_comes_back(coordinator_client, trio):
    copy = trio["copy"].user
    confirm = coordinator_client.get(f"/coordinator/accounts/{copy.pk}/delete/?back=duplicates")
    assert 'name="back" value="duplicates"' in confirm.content.decode()

    response = coordinator_client.post(f"/coordinator/accounts/{copy.pk}/delete/", {"back": "duplicates"})

    assert response.status_code == 302
    assert response.url == URL
    assert not User.objects.filter(pk=copy.pk).exists()
    assert AuditLog.objects.filter(action="account.deleted_by_coordinator", target_id=str(copy.pk)).exists()


# --- „Usuń” w wierszu (ACC-DUP-02) ---------------------------------------------------------------


def test_inline_delete_with_confirmation_comes_back_to_the_group(coordinator_client, trio):
    copy = trio["copy"].user

    response = coordinator_client.post(one_url(copy), {"confirm": copy.pk, "seen_login": "", "group": "1"})

    assert response.status_code == 302
    assert response.url == f"{URL}#grupa-1"
    assert not User.objects.filter(pk=copy.pk).exists()
    assert AuditLog.objects.filter(action="account.deleted_by_coordinator", target_id=str(copy.pk)).exists()


def test_inline_delete_without_confirmation_is_refused(coordinator_client, trio):
    copy = trio["copy"].user

    for data in ({}, {"confirm": "tak"}, {"confirm": str(trio["late"].user.pk)}):
        response = coordinator_client.post(one_url(copy), {"seen_login": "", **data}, follow=True)
        assert "wymaga potwierdzenia" in response.content.decode()

    assert User.objects.filter(pk=copy.pk).exists()
    assert not AuditLog.objects.filter(action="account.deleted_by_coordinator").exists()


def test_inline_delete_of_a_used_account_is_allowed_with_the_stamp_it_showed(coordinator_client, trio):
    keeper = trio["keeper"].user

    response = coordinator_client.post(
        one_url(keeper), {"confirm": keeper.pk, "seen_login": login_stamp(keeper)}, follow=True
    )

    assert not User.objects.filter(pk=keeper.pk).exists()
    assert "zostało usunięte" in response.content.decode()


def test_inline_delete_skips_an_account_that_logged_in_after_the_screen(coordinator_client, trio):
    copy = trio["copy"].user
    User.objects.filter(pk=copy.pk).update(last_login=timezone.now())

    response = coordinator_client.post(one_url(copy), {"confirm": copy.pk, "seen_login": ""}, follow=True)

    assert User.objects.filter(pk=copy.pk).exists()
    assert "nie zostało usunięte" in response.content.decode()


def test_inline_delete_never_takes_the_last_account_of_a_person(coordinator_client, competition):
    """Grupa dwóch kont: po usunięciu jednego drugie nie jest już w żadnej grupie – zostaje."""
    first = person(competition, email="anna@gmail.com")
    second = person(competition, email="anna@gmail.con")

    coordinator_client.post(one_url(first.user), {"confirm": first.user.pk, "seen_login": ""})
    coordinator_client.post(one_url(second.user), {"confirm": second.user.pk, "seen_login": ""})

    assert not User.objects.filter(pk=first.user.pk).exists()
    assert User.objects.filter(pk=second.user.pk).exists()


def test_inline_delete_of_an_account_from_another_competition_is_not_found(
    client_for, competition, other_competition, trio
):
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(coordinator)
    stranger = ParticipantFactory(competition=other_competition)

    response = client.post(one_url(stranger.user), {"confirm": stranger.user.pk, "seen_login": ""})

    assert response.status_code == 404
    assert User.objects.filter(pk=stranger.user.pk).exists()


def test_unknown_back_value_falls_back_to_the_accounts_list(coordinator_client, trio):
    copy = trio["copy"].user

    response = coordinator_client.post(
        f"/coordinator/accounts/{copy.pk}/delete/", {"back": "https://evil.test/"}
    )

    assert response.url == "/coordinator/accounts/"


# --- zbiorcze usunięcie --------------------------------------------------------------------------


def test_bulk_delete_confirms_the_exact_list_then_skips_an_account_that_logged_in(coordinator_client, trio):
    ids = [trio["copy"].user.pk, trio["late"].user.pk]

    confirm = coordinator_client.post(DELETE_URL, {"account": ids})
    body = confirm.content.decode()
    assert confirm.status_code == 200
    assert trio["copy"].user.email in body
    assert trio["late"].user.email in body
    assert 'name="confirm"' in body
    assert User.objects.filter(pk__in=ids).count() == 2  # pierwszy krok niczego nie usuwa

    # Między ekranem potwierdzenia a kliknięciem uczeń zalogował się na drugą kopię.
    User.objects.filter(pk=trio["late"].user.pk).update(last_login=timezone.now())
    response = coordinator_client.post(DELETE_URL, {"account": ids, "confirm": "1"}, follow=True)
    body = response.content.decode()

    assert not User.objects.filter(pk=trio["copy"].user.pk).exists()
    assert User.objects.filter(pk__in=[trio["late"].user.pk, trio["keeper"].user.pk]).count() == 2
    assert "Usunięto kont: 1." in body
    assert "Pominięto kont: 1" in body
    assert AuditLog.objects.filter(action="account.deleted_by_coordinator").count() == 1


def test_bulk_confirmation_marks_accounts_that_are_no_longer_candidates(coordinator_client, trio):
    response = coordinator_client.post(DELETE_URL, {"account": [trio["keeper"].user.pk]})
    body = response.content.decode()

    assert "Zostaną pominięte" in body
    assert "Żadne z wybranych kont nie jest już kandydatem" in body


def test_bulk_delete_never_removes_the_coordinator(coordinator_client, coordinator, competition):
    """Konto koordynatora z profilem uczestnika: ani kandydat, ani usuwalne tą drogą."""
    ParticipantFactory(competition=competition, user=coordinator, school="LO nr 5 w Bielsku-Białej")
    coordinator.first_name, coordinator.last_name = "Anna", "Nowak"
    coordinator.save(update_fields=["first_name", "last_name"])
    person(competition, logged_in=True)

    coordinator_client.post(DELETE_URL, {"account": [coordinator.pk], "confirm": "1"})

    assert User.objects.filter(pk=coordinator.pk).exists()


# --- eksport i koszt -----------------------------------------------------------------------------


def test_export_lists_every_account_and_is_audited(coordinator_client, trio):
    response = coordinator_client.get(EXPORT_URL)
    content = b"".join(response.streaming_content).decode("utf-8")

    assert response["Content-Type"].startswith("text/csv")
    for profile in trio.values():
        assert profile.user.email in content
    assert "kandydat do usunięcia" in content
    entry = AuditLog.objects.get(action="account.duplicates_exported")
    assert (entry.diff["groups"], entry.diff["rows"]) == (1, 3)


def test_page_query_count_does_not_grow_with_groups(
    coordinator_client, competition, trio, django_assert_max_num_queries
):
    coordinator_client.get(URL)  # rozgrzewka pamięci podręcznych procesu

    with django_assert_max_num_queries(PAGE_QUERY_CEILING) as few:
        assert coordinator_client.get(URL).status_code == 200

    for n in range(5):
        person(competition, first=f"Osoba{n}", logged_in=True)
        person(competition, first=f"Osoba{n}")
        UserFactory()
    with django_assert_max_num_queries(PAGE_QUERY_CEILING) as many:
        assert coordinator_client.get(URL).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)
