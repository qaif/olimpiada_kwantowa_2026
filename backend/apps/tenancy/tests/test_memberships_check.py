"""``manage.py check_memberships`` — pre-flight przed przełączeniem ``memberships_enforced``.

Komenda odpowiada na jedno pytanie operatora: **kto straci dostęp**, jeżeli o roli przestanie
rozstrzygać globalna grupa Django, a zacznie wiersz ``accounts.Membership`` konkursu (§ 3.8).
Testy pilnują trzech rzeczy, bo z nich bierze się wartość tej odpowiedzi:

1. **rozjazd widać, zanim zaszkodzi** — i widać go kodem wyjścia, nie tylko zdaniem w logu,
2. ``--fix`` **dopisuje, nigdy nie kasuje** — narzędzie ma dopiąć backfill, a nie porządkować role,
3. przy wielu konkursach **nikt nie dostaje cudzej roli**: członek globalnej grupy bez śladu
   w konkursie nie jest do niego przypisywany, bo zgadywanie znaczyłoby wgląd w cudze prace.
"""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import CompetitionRole, Membership
from apps.accounts.tests.factories import ParticipantFactory, UserFactory

pytestmark = pytest.mark.django_db


def run(**options) -> str:
    from io import StringIO

    out = StringIO()
    call_command("check_memberships", stdout=out, **options)
    return out.getvalue()


def test_group_member_without_membership_is_reported_and_stops_the_command(competition):
    """Recenzent z grupy, ale bez członkostwa — dokładnie ta osoba straci panel po przełączeniu."""
    user = UserFactory(email="recenzent@example.test", groups=[CompetitionRole.REVIEWER])

    with pytest.raises(CommandError, match="memberships_enforced"):
        run()

    assert not Membership.objects.filter(user=user, competition=competition).exists()


def test_fix_creates_the_missing_memberships(competition):
    user = UserFactory(email="recenzent@example.test", groups=[CompetitionRole.REVIEWER])

    output = run(fix=True)

    assert Membership.objects.filter(
        user=user, competition=competition, role=CompetitionRole.REVIEWER
    ).exists()
    assert "dopisane: 1" in output


def test_fix_never_deletes_an_existing_membership(competition):
    """Członkostwo bez grupy Django zostaje: odbieranie roli jest decyzją, a nie skutkiem ubocznym."""
    user = UserFactory(email="bylareczenzent@example.test")
    Membership.objects.create(user=user, competition=competition, role=CompetitionRole.REVIEWER)

    run(fix=True)

    assert Membership.objects.filter(user=user, competition=competition).count() == 1


def test_membership_without_group_is_information_not_a_difference(competition):
    """Rola po przełączeniu zadziała; nie zadziała ``/cms/``, bo panel wisi na grupie Django."""
    UserFactory(email="koordynator@example.test")
    Membership.objects.create(
        user=UserFactory(email="koordynator2@example.test"),
        competition=competition,
        role=CompetitionRole.COORDINATOR,
    )

    output = run()

    assert "członkostw bez grupy Django" in output


def test_matching_state_is_quiet(competition):
    user = UserFactory(email="recenzent@example.test", groups=[CompetitionRole.REVIEWER])
    Membership.objects.create(user=user, competition=competition, role=CompetitionRole.REVIEWER)

    output = run()

    assert "brakujące członkostwa: 0" in output


def test_inactive_accounts_are_ignored(competition):  # noqa: ARG001 - fikstura konkursu
    """Konto zablokowane nie ma roli ani przed przełączeniem, ani po nim — nie ma czego stracić."""
    UserFactory(email="zablokowany@example.test", groups=[CompetitionRole.REVIEWER], is_active=False)

    output = run()

    assert "brakujące członkostwa: 0" in output


def test_participant_row_counts_even_without_the_group(competition):
    """Profil uczestnika konkursu jest mocniejszym dowodem udziału niż globalna grupa."""
    participant = ParticipantFactory(competition=competition)
    participant.user.groups.clear()

    with pytest.raises(CommandError, match="memberships_enforced"):
        run()

    run(fix=True)
    assert Membership.objects.filter(
        user=participant.user, competition=competition, role=CompetitionRole.PARTICIPANT
    ).exists()


def test_second_competition_does_not_borrow_group_members(competition, other_competition):
    """Członek globalnej grupy bez śladu w konkursie B nie jest do konkursu B przypisywany.

    To jest cała ostrożność tej komendy: przypisanie „na wszelki wypadek” dałoby recenzentowi
    konkursu A wgląd w prace konkursu B — czyli dokładnie to, czemu członkostwa mają zapobiec.
    """
    participant = ParticipantFactory(competition=competition)

    run(competition=other_competition.slug, fix=True)

    assert not Membership.objects.filter(user=participant.user, competition=other_competition).exists()
    # A w swoim konkursie ta sama osoba członkostwo dostaje.
    run(competition=competition.slug, fix=True)
    assert Membership.objects.filter(user=participant.user, competition=competition).exists()


def test_membership_in_one_competition_ties_the_person_to_it_only(competition, other_competition):
    """Ślad w konkursie to także samo członkostwo — w innej roli."""
    user = UserFactory(email="komisja@example.test", groups=[CompetitionRole.APPEALS])
    Membership.objects.create(user=user, competition=competition, role=CompetitionRole.REVIEWER)

    run(fix=True)

    assert Membership.objects.filter(
        user=user, competition=competition, role=CompetitionRole.APPEALS
    ).exists()
    assert not Membership.objects.filter(user=user, competition=other_competition).exists()


def test_unknown_slug_refuses(competition):  # noqa: ARG001 - fikstura konkursu
    with pytest.raises(CommandError, match="Nie ma konkursu"):
        run(competition="nieistniejacy")
