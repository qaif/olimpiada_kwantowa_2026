"""Zdublowane konta uczestników (ACC-DUP-01): grupowanie, sugestia, zbiorcze usunięcie.

Czego pilnują te testy poza samym „znalazł grupę”:

- **zakres** – profile innego konkursu i konta zanonimizowane nie wchodzą do grup nigdy,
- **ostrożność sugestii** – kandydat do usunięcia istnieje wyłącznie obok dokładnie jednego
  konta używanego i tylko wtedy, gdy usunięcie nie zabierze niczego poza pustą kopią,
- **usuwanie przelicza warunki w chwili usuwania** – konto, które zdążyło się zalogować, zostaje,
- **koszt** – liczba zapytań nie rośnie z liczbą osób.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.duplicates import (
    CANDIDATE,
    DECIDE,
    KEEP,
    delete_candidates,
    find_duplicate_groups,
    normalize_email,
    normalize_text,
)
from apps.accounts.models import CompetitionRole, User
from apps.accounts.tests.factories import (
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
)
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.schools.custom import CustomInstitution
from apps.schools.models import InstitutionType
from apps.schools.tests.factories import SchoolFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def person(
    competition, *, first="Anna", last="Nowak", school="LO nr 5 w Bielsku-Białej", logged_in=False, **kw
):
    """Profil uczestnika z kontem; ``logged_in`` ustawia ``last_login`` (konto „używane”)."""
    user_fields = {f"user__{key}": value for key, value in kw.pop("user", {}).items()}
    profile = ParticipantFactory(
        competition=competition,
        user__first_name=first,
        user__last_name=last,
        user__last_login=timezone.now() if logged_in else None,
        school=school,
        **user_fields,
        **kw,
    )
    return profile


def groups_of(competition):
    return find_duplicate_groups(competition).groups


def suggestions(group) -> dict[int, str]:
    return {account.participant.pk: account.suggestion for account in group.accounts}


# --- normalizacja --------------------------------------------------------------------------------


def test_normalize_text_folds_case_spaces_dots_quotes_and_unicode_forms():
    assert normalize_text("  Anna   NOWAK ") == "anna nowak"
    assert normalize_text("LO „Batory” im. S.") == normalize_text('lo "batory" im s')
    # NFKC: litery pełnej szerokości i „ł” zapisane tak samo po normalizacji.
    assert normalize_text("ＡＮＮＡ") == "anna"
    assert normalize_text("Maria-Anna") == "maria-anna"
    assert normalize_text(None) == ""


def test_normalize_email_fixes_known_domain_typos_only():
    assert normalize_email("Jan@Gmail.con") == "jan@gmail.com"
    assert normalize_email("jan@gmial.com") == "jan@gmail.com"
    assert normalize_email("jan@wp.p") == "jan@wp.pl"
    assert normalize_email("jan@szkola.con") == "jan@szkola.com"
    # Dwie prawdziwe, różne domeny zostają różne.
    assert normalize_email("jan@o2.pl") != normalize_email("jan@op.pl")


# --- grupowanie ----------------------------------------------------------------------------------


def test_same_name_and_school_text_form_a_group_despite_case_spaces_and_dots(competition):
    first = person(competition, first="Anna", last="Nowak", school="LO nr 5 w Bielsku-Białej")
    second = person(competition, first=" anna ", last="NOWAK", school="lo  nr. 5 w bielsku-białej")
    person(competition, first="Anna", last="Nowak", school="LO nr 6 w Bielsku-Białej")
    person(competition, first="Ola", last="Nowak", school="LO nr 5 w Bielsku-Białej")

    groups = groups_of(competition)

    assert len(groups) == 1
    assert {account.participant.pk for account in groups[0].accounts} == {first.pk, second.pk}


def test_school_from_the_register_groups_by_reference_not_by_text(competition):
    school = SchoolFactory()
    first = person(competition, school="I LO", school_ref=school)
    second = person(competition, school="Pierwsze liceum (wpisane inaczej)", school_ref=school)
    # Ten sam tekst, ale bez odnośnika – inny rodzaj klucza, więc nie dołącza do grupy z wykazu.
    person(competition, school="I LO")

    groups = groups_of(competition)

    assert len(groups) == 1
    assert {account.participant.pk for account in groups[0].accounts} == {first.pk, second.pk}


def test_custom_institution_groups_by_reference(competition):
    institution = CustomInstitution.objects.create(
        competition=competition,
        name="UNIWERSYTET X",
        city="Kraków",
        institution_type=InstitutionType.UNIVERSITY,
    )
    first = person(competition, school="UX", custom_institution_ref=institution)
    second = person(competition, school="Uniwersytet X", custom_institution_ref=institution)

    groups = groups_of(competition)

    assert [{a.participant.pk for a in group.accounts} for group in groups] == [{first.pk, second.pk}]


def test_competitions_do_not_mix(competition, other_competition):
    person(competition)
    person(other_competition)

    assert groups_of(competition) == []
    assert groups_of(other_competition) == []


def test_anonymised_and_nameless_profiles_are_left_out(competition):
    person(competition)
    person(competition, user={"email": "deleted-77@invalid.olimpiadakwantowa.pl"})
    person(competition, first="", last="Nowak")
    person(competition, first="", last="Nowak")

    assert groups_of(competition) == []


# --- sugestie ------------------------------------------------------------------------------------


def test_exactly_one_logged_in_account_is_kept_and_the_others_are_candidates(competition):
    keeper = person(competition, logged_in=True)
    copy_a = person(competition)
    copy_b = person(competition)

    (group,) = groups_of(competition)

    assert suggestions(group) == {keeper.pk: KEEP, copy_a.pk: CANDIDATE, copy_b.pk: CANDIDATE}


def test_no_used_account_means_no_candidate(competition):
    first = person(competition)
    second = person(competition)

    (group,) = groups_of(competition)

    assert suggestions(group) == {first.pk: DECIDE, second.pk: DECIDE}
    assert "żadne" in group.accounts[0].reason


def test_two_used_accounts_are_left_to_the_coordinator_but_an_empty_copy_stays_a_candidate(competition):
    """Które z dwóch używanych kont jest prawdziwe, wie koordynator; pusta kopia jest zbędna i tak."""
    first = person(competition, logged_in=True)
    second = person(competition, logged_in=True)
    third = person(competition)

    (group,) = groups_of(competition)

    assert suggestions(group) == {first.pk: DECIDE, second.pk: DECIDE, third.pk: CANDIDATE}
    assert "kilka używanych kont" in group.accounts[0].reason


def test_a_competition_stage_entry_makes_an_account_used_but_training_does_not(competition):
    elim = StageFactory(competition=competition, kind=StageKind.ELIM)
    training = StageFactory(competition=competition, edition=elim.edition, kind=StageKind.TRAINING)
    keeper = person(competition)
    StageEntryFactory(competition=competition, participant=keeper, stage=elim)
    copy = person(competition)
    StageEntryFactory(competition=competition, participant=copy, stage=training)

    (group,) = groups_of(competition)
    by_pk = {account.participant.pk: account for account in group.accounts}

    assert suggestions(group) == {keeper.pk: KEEP, copy.pk: CANDIDATE}
    assert by_pk[keeper.pk].competition_stages == [elim.display_name]
    assert by_pk[copy.pk].training_stages == [training.display_name]
    assert by_pk[copy.pk].competition_stages == []


def test_works_make_an_account_used(competition):
    keeper = person(competition)
    SubmissionFactory(competition=competition, entry__participant=keeper)
    copy = person(competition)

    (group,) = groups_of(competition)

    assert suggestions(group) == {keeper.pk: KEEP, copy.pk: CANDIDATE}
    assert {a.participant.pk: a.works for a in group.accounts} == {keeper.pk: 1, copy.pk: 0}


def test_an_account_with_other_roles_is_never_a_candidate(competition, other_competition):
    keeper = person(competition, logged_in=True)
    reviewer_copy = person(competition)
    CommitteeMemberFactory(competition=competition, user=reviewer_copy.user)
    elsewhere_copy = person(competition)
    ParticipantFactory(competition=other_competition, user=elsewhere_copy.user)
    member_copy = person(competition)
    grant_membership(member_copy.user, other_competition, CompetitionRole.PARTICIPANT)

    (group,) = groups_of(competition)

    assert suggestions(group) == {
        keeper.pk: KEEP,
        reviewer_copy.pk: DECIDE,
        elsewhere_copy.pk: DECIDE,
        member_copy.pk: DECIDE,
    }
    assert all("inne role" in a.reason for a in group.accounts if a.participant.pk != keeper.pk)


def test_email_typo_signal_lists_pairs_outside_name_groups(competition):
    person(competition, first="Jan", last="Kowalski", user={"email": "jan.k@gmail.com"})
    person(competition, first="Janek", last="Kowalski", user={"email": "jan.k@gmail.con"})
    # Ta para jest już grupą imienno-szkolną – drugi raz w sekcji słabszego sygnału jej nie ma.
    person(competition, user={"email": "anna@wp.pl"}, logged_in=True)
    person(competition, user={"email": "anna@wp.p"})

    report = find_duplicate_groups(competition)

    assert [suspect.normalized for suspect in report.email_suspects] == ["jan.k@gmail.com"]
    assert len(report.groups) == 1


def test_query_count_does_not_grow_with_people(competition, django_assert_max_num_queries):
    names = iter(f"Osoba{n}" for n in range(100))

    def add_group():
        name = next(names)
        person(competition, first=name, logged_in=True)
        person(competition, first=name)

    add_group()
    with django_assert_max_num_queries(10) as few:
        assert len(groups_of(competition)) == 1
    for _ in range(6):
        add_group()
    with django_assert_max_num_queries(10) as many:
        assert len(groups_of(competition)) == 7

    assert len(many.captured_queries) == len(few.captured_queries)


# --- usuwanie ------------------------------------------------------------------------------------


def test_delete_candidates_deletes_copies_and_skips_one_that_logged_in_meanwhile(competition):
    coordinator = CoordinatorFactory()
    keeper = person(competition, logged_in=True)
    copy = person(competition)
    late = person(competition)
    ids = [copy.user.pk, late.user.pk]
    # Między wyświetleniem ekranu a kliknięciem uczeń zalogował się na „kopię”.
    User.objects.filter(pk=late.user.pk).update(last_login=timezone.now())

    result = delete_candidates(competition, ids, actor=coordinator)

    assert result.deleted == [copy.user.email]
    assert result.skipped == [late.user.email]
    assert not User.objects.filter(pk=copy.user.pk).exists()
    assert User.objects.filter(pk__in=[late.user.pk, keeper.user.pk]).count() == 2
    assert AuditLog.objects.filter(
        action="account.deleted_by_coordinator", target_id=str(copy.user.pk)
    ).exists()


def test_delete_candidates_never_takes_the_last_account(competition):
    """Konto do zachowania usunięte w międzyczasie – kopia przestaje być kandydatem."""
    coordinator = CoordinatorFactory()
    keeper = person(competition, logged_in=True)
    copy = person(competition)
    keeper.user.delete()

    result = delete_candidates(competition, [copy.user.pk], actor=coordinator)

    assert result.skipped == [copy.user.email]
    assert User.objects.filter(pk=copy.user.pk).exists()


def test_a_candidate_with_training_entry_is_anonymised_not_erased(competition):
    coordinator = CoordinatorFactory()
    training = StageFactory(competition=competition, kind=StageKind.TRAINING)
    person(competition, logged_in=True)
    copy = person(competition)
    StageEntryFactory(competition=competition, participant=copy, stage=training)

    result = delete_candidates(competition, [copy.user.pk], actor=coordinator)

    assert result.anonymised == [copy.user.email]
    assert User.objects.get(pk=copy.user.pk).email.startswith("deleted-")
