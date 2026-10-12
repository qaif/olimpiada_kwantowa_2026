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


# --- kontrola systemowa ``tenancy.E001`` (poprawka po audycie izolacji, 01.10.2026) -------------------
#
# Komenda wyżej odpowiada na pytanie „czy wolno przełączyć”; kontrola – „czy instalacja nie stoi
# w stanie, w którym przełączyć było trzeba”. Dwa aktywne konkursy z rolami z globalnych grup to
# role jednego organizatora w panelach drugiego.


def system_check_ids(*, with_database: bool = True) -> list[str]:
    from django.core import checks

    from apps.tenancy.checks import check_memberships_enforced

    databases = ["default"] if with_database else None
    return [
        message.id
        for message in check_memberships_enforced(databases=databases)
        if message.level >= checks.ERROR
    ]


def test_a_single_competition_with_groups_is_the_safe_state_of_today(competition):  # noqa: ARG001
    """Dzisiejsza produkcja: jeden konkurs, flaga wyłączona – kontrola milczy."""
    assert system_check_ids() == []


def test_two_active_competitions_with_the_flag_off_are_an_error(competition, other_competition):  # noqa: ARG001
    assert system_check_ids() == ["tenancy.E001"]


def test_the_error_names_only_the_competitions_that_need_switching(competition, other_competition):
    from apps.tenancy.checks import check_memberships_enforced
    from apps.tenancy.tests.factories import enforce_memberships

    enforce_memberships(other_competition)

    [message] = check_memberships_enforced(databases=["default"])
    assert competition.slug in message.msg
    assert other_competition.slug not in message.msg
    assert "check_memberships" in message.hint


def test_every_competition_with_the_flag_on_is_quiet(competition, other_competition):
    from apps.tenancy.tests.factories import enforce_memberships

    enforce_memberships(competition)
    enforce_memberships(other_competition)

    assert system_check_ids() == []


def test_an_inactive_competition_does_not_count(competition, other_competition):  # noqa: ARG001
    """Pod adresem konkursu nieaktywnego nikt nie dostaje paneli – jego flaga niczego nie otwiera."""
    other_competition.is_active = False
    other_competition.save(update_fields=["is_active"])

    assert system_check_ids() == []


def test_the_check_runs_only_with_the_database(competition, other_competition):  # noqa: ARG001
    """``Tags.database``: zwykłe ``manage.py check`` (i każda komenda, w tym ``check_memberships``) jej
    nie uruchamia – tylko ``migrate`` i ``check --database default``."""
    assert system_check_ids(with_database=False) == []


def test_a_database_without_tables_is_not_an_error(competition, monkeypatch):  # noqa: ARG001
    """Pierwsze ``migrate``: tabeli konkursów jeszcze nie ma, a to nie jest błąd konfiguracji."""
    from django.db import ProgrammingError

    from apps.tenancy import checks as tenancy_checks

    def missing_table():
        raise ProgrammingError('relation "tenancy_competition" does not exist')

    monkeypatch.setattr(tenancy_checks, "unscoped_competitions", missing_table)

    assert system_check_ids() == []


def test_check_command_with_the_database_reports_the_error(competition, other_competition):  # noqa: ARG001
    from django.core.management.base import SystemCheckError

    with pytest.raises(SystemCheckError, match="tenancy.E001"):
        call_command("check", "--database", "default")


# --- Competition.clean: ta sama reguła po stronie zapisu (audyt W4, 10.10.2026) ----------------
#
# Kontrola wyżej łapie stan dopiero przy ``migrate`` – czyli wtedy, gdy zatrzymuje kontener ``web``
# wszystkim konkursom. ``Competition.clean`` odmawia **przejścia** w ten stan w chwili zapisu
# (``/admin/``, komendy z ``full_clean``), a stan zastany przepuszcza, żeby dało się go naprawić.


def _flags(competition, *, enforced: bool) -> None:
    competition.feature_flags = {**(competition.feature_flags or {}), "memberships_enforced": enforced}
    competition.save(update_fields=["feature_flags"])


def test_clean_refuses_turning_the_flag_off_next_to_another_active_competition(
    competition, other_competition
):
    from django.core.exceptions import ValidationError

    _flags(competition, enforced=True)
    _flags(other_competition, enforced=True)
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": False}

    with pytest.raises(ValidationError) as caught:
        competition.full_clean()

    assert "feature_flags" in caught.value.message_dict
    assert "memberships_enforced" in caught.value.message_dict["feature_flags"][0]


def test_clean_allows_turning_the_flag_off_in_a_single_competition(competition):
    """Dzisiejsza produkcja: role z grup są rolami w jedynym konkursie – nie ma obok kogo przeciekać."""
    _flags(competition, enforced=True)
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": False}

    competition.full_clean()


def test_clean_allows_turning_the_flag_off_next_to_an_inactive_competition(competition, other_competition):
    _flags(competition, enforced=True)
    other_competition.is_active = False
    other_competition.save(update_fields=["is_active"])
    competition.feature_flags = {**competition.feature_flags, "memberships_enforced": False}

    competition.full_clean()


def test_clean_refuses_activating_a_competition_next_to_an_unscoped_one(competition, other_competition):
    """Druga strona tej samej reguły: włączany konkurs przeciekałby do tamtego, nie odwrotnie."""
    from django.core.exceptions import ValidationError

    _flags(competition, enforced=False)
    _flags(other_competition, enforced=True)
    other_competition.is_active = False
    other_competition.save(update_fields=["is_active"])
    other_competition.is_active = True

    with pytest.raises(ValidationError) as caught:
        other_competition.full_clean()

    assert "is_active" in caught.value.message_dict
    assert competition.slug in caught.value.message_dict["is_active"][0]


def test_clean_refuses_reactivating_an_unscoped_competition_next_to_another(competition, other_competition):
    from django.core.exceptions import ValidationError

    _flags(competition, enforced=True)
    _flags(other_competition, enforced=False)
    other_competition.is_active = False
    other_competition.save(update_fields=["is_active"])
    other_competition.is_active = True

    with pytest.raises(ValidationError) as caught:
        other_competition.full_clean()

    assert "feature_flags" in caught.value.message_dict


def test_clean_lets_an_inherited_unscoped_state_be_saved(competition, other_competition):
    """Stan zastany (dwa aktywne, flagi wyłączone) zgłasza ``tenancy.E001`` – ale zapis innej zmiany
    w ``/admin/`` musi przejść, inaczej operator nie mógłby nawet poprawić nazwy konkursu."""
    competition.short_name = "Inna nazwa"

    competition.full_clean()
