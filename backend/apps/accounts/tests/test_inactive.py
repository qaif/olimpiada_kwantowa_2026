"""Nieaktywne konta uczestników (ACC-DUP-02): filtr, zakres, liczniki i bezpieczne usunięcie.

Czego pilnują te testy:

- **granice filtra** – „od N dni” jest domknięte (logowanie dokładnie N dni temu = na liście), „nigdy”
  to wyłącznie ``last_login IS NULL``, wiek konta tak samo domknięty,
- **zakres** – inny konkurs, konta zanonimizowane, koordynator i superużytkownik nie wchodzą nigdy;
  konto z innymi rolami wchodzi, ale z etykietą ról i bez usunięcia zbiorczego,
- **usuwanie przelicza filtr w chwili usuwania** – konto, które zdążyło się zalogować, zostaje,
- **koszt** – liczba zapytań nie rośnie z liczbą kont.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.http import QueryDict
from django.utils import timezone

from apps.accounts.account_cleanup import login_stamp
from apps.accounts.inactive import (
    DEFAULT_JOINED_DAYS,
    InactiveFilter,
    bulk_blocker,
    counters,
    delete_inactive,
    delete_inactive_account,
    facts_in_order,
    inactive_participants,
)
from apps.accounts.models import User
from apps.accounts.tests.factories import CommitteeMemberFactory, CoordinatorFactory, ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def member(competition, *, joined_days_ago=30, last_login=None, **kw):
    """Profil uczestnika założony ``joined_days_ago`` dni temu, z podanym ostatnim logowaniem."""
    user_fields = {f"user__{key}": value for key, value in kw.pop("user", {}).items()}
    return ParticipantFactory(
        competition=competition,
        user__date_joined=NOW - timedelta(days=joined_days_ago),
        user__last_login=last_login,
        **user_fields,
        **kw,
    )


def listed(competition, flt: InactiveFilter) -> set[int]:
    return set(inactive_participants(competition, flt, now=NOW).values_list("pk", flat=True))


def parse(query: str):
    return InactiveFilter.parse(QueryDict(query))


# --- parsowanie filtra ---------------------------------------------------------------------------


def test_missing_parameters_give_the_defaults():
    flt, errors = parse("")

    assert errors == []
    assert flt == InactiveFilter(login_days=None, joined_days=DEFAULT_JOINED_DAYS, activation="")
    assert flt.never


@pytest.mark.parametrize(
    "query",
    [
        "login=days",
        "login=days&days=0",
        "login=days&days=3651",
        "login=days&days=abc",
        "login=days&days=-5",
        "joined=-1",
        "joined=3651",
        "joined=x",
        "activation=maybe",
        "login=sometimes",
    ],
)
def test_bad_values_are_errors_not_defaults(query):
    flt, errors = parse(query)

    assert flt is None
    assert errors


def test_valid_values_round_trip_through_the_query_string():
    flt, errors = parse("login=days&days=3650&joined=0&activation=unverified")

    assert errors == []
    assert (flt.login_days, flt.joined_days, flt.activation) == (3650, 0, "unverified")
    assert parse(flt.querystring())[0] == flt
    assert "od 3650 dni" in flt.describe()


def test_days_are_ignored_when_the_filter_is_never():
    flt, _ = parse("login=never&days=abc")

    assert flt.never
    assert "days" not in flt.querystring()


# --- filtr ---------------------------------------------------------------------------------------


def test_never_means_no_login_at_all(competition):
    never = member(competition)
    long_ago = member(competition, last_login=NOW - timedelta(days=900))

    assert listed(competition, InactiveFilter()) == {never.pk}
    assert long_ago.pk in listed(competition, InactiveFilter(login_days=365))


def test_days_boundary_is_inclusive_and_includes_never(competition):
    never = member(competition)
    exactly = member(competition, last_login=NOW - timedelta(days=30))
    just_inside = member(competition, last_login=NOW - timedelta(days=30) + timedelta(seconds=1))
    recent = member(competition, last_login=NOW - timedelta(days=1))

    result = listed(competition, InactiveFilter(login_days=30))

    assert result == {never.pk, exactly.pk}
    assert just_inside.pk not in result
    assert recent.pk not in result


def test_account_age_boundary_is_inclusive_and_zero_disables_it(competition):
    old_enough = member(competition, joined_days_ago=7)
    member(competition, joined_days_ago=0)
    fresh = ParticipantFactory(
        competition=competition, user__date_joined=NOW - timedelta(days=7) + timedelta(seconds=1)
    )

    assert listed(competition, InactiveFilter(joined_days=7)) == {old_enough.pk}
    assert fresh.pk in listed(competition, InactiveFilter(joined_days=0))
    assert len(listed(competition, InactiveFilter(joined_days=0))) == 3


def test_activation_filter(competition):
    verified = member(competition)
    pending = member(competition, user={"email_verified_at": None, "is_active": False})

    assert listed(competition, InactiveFilter(activation="verified")) == {verified.pk}
    assert listed(competition, InactiveFilter(activation="unverified")) == {pending.pk}
    assert listed(competition, InactiveFilter()) == {verified.pk, pending.pk}


def test_scope_leaves_out_other_competitions_anonymised_coordinators_and_superusers(
    competition, other_competition
):
    mine = member(competition)
    member(other_competition)
    member(competition, user={"email": "deleted-77@invalid.olimpiadakwantowa.pl"})
    coordinator = CoordinatorFactory(date_joined=NOW - timedelta(days=30))
    ParticipantFactory(competition=competition, user=coordinator)
    member(competition, user={"is_superuser": True})

    assert listed(competition, InactiveFilter()) == {mine.pk}


def test_an_account_with_other_roles_is_listed_with_labels_but_not_for_bulk(competition):
    reviewer = member(competition)
    CommitteeMemberFactory(competition=competition, user=reviewer.user)

    (account,) = facts_in_order(competition, [reviewer.pk])

    assert account.other_role_labels == ["komitet"]
    assert "inne role" in bulk_blocker(account)


def test_counters_in_one_query(competition, django_assert_num_queries):
    member(competition)
    member(competition, user={"email_verified_at": None, "is_active": False})
    traced = member(competition)
    StageEntryFactory(competition=competition, participant=traced)
    training = member(competition)
    StageEntryFactory(
        competition=competition,
        participant=training,
        stage=StageFactory(competition=competition, kind=StageKind.TRAINING),
    )

    with django_assert_num_queries(1):
        counts = counters(inactive_participants(competition, InactiveFilter(), now=NOW))

    assert counts == {"total": 4, "never": 4, "unverified": 1, "with_trace": 1}


def test_facts_cost_does_not_grow_with_accounts(competition, django_assert_max_num_queries):
    first = [member(competition).pk]
    with django_assert_max_num_queries(10) as few:
        facts_in_order(competition, first)
    many = first + [member(competition).pk for _ in range(8)]
    with django_assert_max_num_queries(10) as more:
        assert len(facts_in_order(competition, many)) == 9

    assert len(more.captured_queries) == len(few.captured_queries)


# --- usuwanie ------------------------------------------------------------------------------------


def test_bulk_delete_skips_an_account_that_logged_in_meanwhile(competition):
    coordinator = CoordinatorFactory()
    gone = member(competition)
    late = member(competition)
    # Między ekranem potwierdzenia a kliknięciem uczeń zalogował się na drugie konto.
    User.objects.filter(pk=late.user.pk).update(last_login=timezone.now())

    result = delete_inactive(competition, [gone.user.pk, late.user.pk], InactiveFilter(), actor=coordinator)

    assert result.deleted == [gone.user.email]
    assert result.skipped == [late.user.email]
    assert not User.objects.filter(pk=gone.user.pk).exists()
    assert User.objects.filter(pk=late.user.pk).exists()
    assert AuditLog.objects.filter(
        action="account.deleted_by_coordinator", target_id=str(gone.user.pk)
    ).exists()


def test_bulk_delete_checks_the_filter_with_days(competition):
    coordinator = CoordinatorFactory()
    stale = member(competition, last_login=NOW - timedelta(days=100))
    fresh = member(competition, last_login=timezone.now() - timedelta(days=5))

    result = delete_inactive(
        competition, [stale.user.pk, fresh.user.pk], InactiveFilter(login_days=90), actor=coordinator
    )

    assert result.deleted == [stale.user.email]
    assert result.skipped == [fresh.user.email]


def test_bulk_delete_skips_accounts_with_other_roles_but_single_delete_allows_them(competition):
    coordinator = CoordinatorFactory()
    reviewer = member(competition)
    CommitteeMemberFactory(competition=competition, user=reviewer.user)

    bulk = delete_inactive(competition, [reviewer.user.pk], InactiveFilter(), actor=coordinator)
    assert bulk.skipped == [reviewer.user.email]
    assert User.objects.filter(pk=reviewer.user.pk).exists()

    single = delete_inactive_account(
        competition, reviewer.user.pk, InactiveFilter(), seen_login="", actor=coordinator
    )
    assert single.deleted == [reviewer.user.email]


def test_single_delete_refuses_a_stale_login_stamp(competition):
    coordinator = CoordinatorFactory()
    profile = member(competition, last_login=NOW - timedelta(days=100))
    flt = InactiveFilter(login_days=30)
    seen = login_stamp(profile.user)
    # Ktoś zalogował się po wyświetleniu listy, ale konto nadal spełnia „od 30 dni” – w chwili
    # usuwania liczy się to, co koordynator widział.
    User.objects.filter(pk=profile.user.pk).update(last_login=NOW - timedelta(days=40))

    result = delete_inactive_account(competition, profile.user.pk, flt, seen_login=seen, actor=coordinator)

    assert result.skipped == [profile.user.email]
    assert User.objects.filter(pk=profile.user.pk).exists()


def test_an_account_with_a_stage_entry_is_anonymised(competition):
    coordinator = CoordinatorFactory()
    profile = member(competition)
    StageEntryFactory(competition=competition, participant=profile)

    result = delete_inactive(competition, [profile.user.pk], InactiveFilter(), actor=coordinator)

    assert result.anonymised == [profile.user.email]
    assert User.objects.get(pk=profile.user.pk).email.startswith("deleted-")


def test_delete_never_reaches_another_competition(competition, other_competition):
    coordinator = CoordinatorFactory()
    stranger = member(other_competition)

    result = delete_inactive(competition, [stranger.user.pk], InactiveFilter(), actor=coordinator)

    assert result.removed == 0
    assert User.objects.filter(pk=stranger.user.pk).exists()
