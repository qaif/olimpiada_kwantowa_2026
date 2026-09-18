"""Drużyny: właściciel wpisu, więzy, flaga i ``entry_owner`` (§ 1.2.3).

Cztery rzeczy, których pilnują te testy, w kolejności wagi:

- **Konkurs #1 nie widzi niczego.** Bez flagi ``team_entries`` nie da się założyć drużyny ani
  wpisać jej do etapu, a każdy istniejący wpis ma właściciela dokładnie tam, gdzie miał go przed
  etapem 2: w ``entry.participant`` (``docs/UNIWERSALNY-ETAP-2.md`` § 0.1).
- **Wpis ma właściciela dokładnie jednego.** To jedyny powód, dla którego ``participant`` wolno
  było znullować, więc więz ``competitions_stageentry_single_owner`` jest sprawdzany z obu stron:
  wpis z dwoma właścicielami i wpis bez żadnego mają odpaść w bazie, a nie dopiero w rankingu.
- **Przeoczenie jest głośne.** ``entry_owner`` dla wpisu bez właściciela podnosi ``AttributeError``
  – ten sam wzorzec, którym etap 1 wymusił poprawki po zamianie ``user.participant``.
- **Konkurs A nie widzi drużyn konkursu B** (§ 5.7), na poziomie querysetu i walidacji składu.

Czego tu **nie** ma i być nie może: liczenia wyników drużynowych. Suma etapu chodzi po wpisach
i dlatego nie zmienia się wcale – dowodem jest snapshot (T28), a nie test w tym pliku.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.models import Participant
from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntry, StageKind, Team, TeamMember
from apps.competitions.services import (
    add_team_member,
    create_team,
    entry_owner,
    missing_stage_kinds,
    register_team_for_stage,
    remove_team_member,
    set_team_captain,
    teams_of,
)
from apps.core.api import DomainError

from .factories import CurrentEditionFactory, StageEntryFactory, StageFactory

pytestmark = pytest.mark.django_db

FEATURE = "team_entries"


def enable_teams(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def teamed(competition):
    """Konkurs z włączonymi zgłoszeniami drużynowymi."""
    return enable_teams(competition)


@pytest.fixture
def edition(teamed):
    return CurrentEditionFactory(competition=teamed)


@pytest.fixture
def team(edition):
    return create_team(edition=edition, name="Kwanty 1", school="LO nr 1")


# --- flaga: Konkurs #1 nie ma jak założyć drużyny -------------------------------------------------


def test_a_competition_without_the_flag_cannot_create_a_team(competition):
    """Bramka stoi w serwisie, czyli na jedynej drodze do drużyny (§ 1.0 (c))."""
    without = CurrentEditionFactory(competition=competition)

    with pytest.raises(DomainError) as exc:
        create_team(edition=without, name="Kwanty 1")

    assert exc.value.machine_code == "TEAM_ENTRIES_DISABLED"
    assert not Team.objects.exists()


def test_a_competition_without_the_flag_cannot_enter_a_team_for_a_stage(teamed):
    """Flagę zdjętą po założeniu drużyny widać od razu: wpisu drużynowego już nie przybędzie."""
    edition = CurrentEditionFactory(competition=teamed)
    team = create_team(edition=edition, name="Kwanty 1")
    stage = StageFactory(edition=edition, kind=StageKind.ELIM)
    teamed.feature_flags = {}
    teamed.save(update_fields=["feature_flags"])

    with pytest.raises(DomainError) as exc:
        register_team_for_stage(team, stage)

    assert exc.value.machine_code == "TEAM_ENTRIES_DISABLED"


# --- kod publiczny: ten sam generator, co przy uczestniku -----------------------------------------


def test_the_team_code_uses_the_competition_prefix(edition, teamed):
    """Kod drużyny stoi w tabeli wyników obok kodów uczestników, więc wygląda tak samo."""
    team = create_team(edition=edition, name="Kwanty 1")

    assert team.public_code.startswith(teamed.public_code_prefix)
    assert len(team.public_code) == len(teamed.public_code_prefix) + 6


def test_two_teams_of_one_edition_cannot_share_a_name(edition):
    create_team(edition=edition, name="Kwanty 1")

    with pytest.raises(DomainError) as exc:
        create_team(edition=edition, name="Kwanty 1")

    assert exc.value.machine_code == "TEAM_NAME_TAKEN"


def test_the_same_name_is_free_again_in_the_next_edition(teamed):
    """Nazwa rozstrzyga w edycji: „Kwanty 1” z dwóch roczników to dwa różne składy."""
    first = CurrentEditionFactory(competition=teamed)
    second = CurrentEditionFactory(competition=teamed, is_current=False)

    create_team(edition=first, name="Kwanty 1")
    later = create_team(edition=second, name="Kwanty 1")

    assert Team.objects.filter(name="Kwanty 1").count() == 2
    assert later.edition_id == second.pk


def test_the_public_code_is_unique_in_the_competition(team):
    """Więz bazy, nie ``SELECT`` przed zapisem – ponawianie w serwisie stoi właśnie na nim."""
    with pytest.raises(IntegrityError, match="competitions_team_public_code"), transaction.atomic():
        Team.objects.create(
            competition=team.competition,
            edition=team.edition,
            name="Kwanty 2",
            public_code=team.public_code,
        )


# --- skład drużyny --------------------------------------------------------------------------------


def test_a_member_joins_the_team(team, teamed):
    participant = ParticipantFactory(competition=teamed)

    member = add_team_member(team, participant, is_captain=True)

    assert member.is_captain
    assert list(teams_of(participant)) == [team]


def test_the_same_participant_joins_only_once(team, teamed):
    participant = ParticipantFactory(competition=teamed)
    add_team_member(team, participant)

    with pytest.raises(DomainError) as exc:
        add_team_member(team, participant)

    assert exc.value.machine_code == "TEAM_MEMBER_EXISTS"


def test_a_second_captain_is_refused(team, teamed):
    """Dwóch kapitanów to nie drużyna o dwóch kapitanach, tylko nieskończone przekazanie funkcji."""
    add_team_member(team, ParticipantFactory(competition=teamed), is_captain=True)

    with pytest.raises(DomainError) as exc:
        add_team_member(team, ParticipantFactory(competition=teamed), is_captain=True)

    assert exc.value.machine_code == "TEAM_CAPTAIN_TAKEN"


def test_the_captaincy_is_handed_over_in_one_step(team, teamed):
    """Najpierw zdjęcie, potem nadanie – inaczej więz pękłby w połowie operacji."""
    first = ParticipantFactory(competition=teamed)
    second = ParticipantFactory(competition=teamed)
    add_team_member(team, first, is_captain=True)
    add_team_member(team, second)

    set_team_captain(team, second)

    captains = TeamMember.objects.filter(team=team, is_captain=True)

    assert list(captains.values_list("participant", flat=True)) == [second.pk]


def test_the_captaincy_cannot_go_to_someone_outside_the_team(team, teamed):
    with pytest.raises(DomainError) as exc:
        set_team_captain(team, ParticipantFactory(competition=teamed))

    assert exc.value.machine_code == "TEAM_MEMBER_NOT_FOUND"


def test_removing_a_member_leaves_the_participant_alone(team, teamed):
    """``PROTECT`` przy członkostwie: ze składu znika członkostwo, a nie profil uczestnika."""
    participant = ParticipantFactory(competition=teamed)
    add_team_member(team, participant)

    remove_team_member(team, participant)

    assert not TeamMember.objects.filter(team=team).exists()
    assert Participant.objects.filter(pk=participant.pk).exists()


def test_two_captains_are_refused_by_the_database_too(team, teamed):
    """Ostatnia linia obrony przed zapisem z pominięciem serwisu."""
    TeamMember.objects.create(team=team, participant=ParticipantFactory(competition=teamed), is_captain=True)

    with pytest.raises(IntegrityError, match="competitions_teammember_single_captain"), transaction.atomic():
        TeamMember.objects.create(
            team=team, participant=ParticipantFactory(competition=teamed), is_captain=True
        )


# --- izolacja konkursów (§ 5.7) -------------------------------------------------------------------


def test_a_team_of_another_competition_is_invisible(team, other_competition):
    assert list(Team.objects.for_competition(team.competition)) == [team]
    assert list(Team.objects.for_competition(other_competition)) == []


def test_a_team_cannot_start_in_another_competitions_edition(teamed, other_competition):
    """Drużyna z kodem organizatora A w zawodach organizatora B nie ma prawa powstać."""
    foreign = CurrentEditionFactory(competition=other_competition, is_current=False)
    team = Team(competition=teamed, edition=foreign, name="Kwanty 1", public_code="OLM-AAAAAA")

    with pytest.raises(ValidationError, match="innego konkursu"):
        team.full_clean()


def test_a_participant_of_another_competition_cannot_join(team, other_competition):
    """Gdyby nie ta reguła, cudzy profil trafiłby do tabeli wyników przez wpis drużyny."""
    stranger = ParticipantFactory(competition=other_competition)

    with pytest.raises(DomainError) as exc:
        add_team_member(team, stranger)

    assert exc.value.machine_code == "TEAM_INVALID"
    assert not TeamMember.objects.exists()


def test_a_team_entry_of_another_competition_is_invisible(team, other_competition):
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)
    entry = register_team_for_stage(team, stage)

    assert list(StageEntry.objects.for_competition(team.competition)) == [entry]
    assert list(StageEntry.objects.for_competition(other_competition)) == []


# --- właściciel wpisu: jeden z dwóch, nigdy oba i nigdy żaden -------------------------------------


def test_an_entry_of_a_participant_is_unchanged(competition):
    """Wpis Konkursu #1 ma właściciela tam, gdzie miał go przed etapem 2."""
    entry = StageEntryFactory(competition=competition)

    assert entry.team_id is None
    assert entry_owner(entry) == entry.participant


def test_a_team_entry_has_no_participant(team):
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)

    entry = register_team_for_stage(team, stage)

    assert entry.participant_id is None
    assert entry_owner(entry) == team
    assert team.public_code in str(entry)


def test_a_team_enters_a_stage_only_once(team):
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)
    register_team_for_stage(team, stage)

    with pytest.raises(DomainError) as exc:
        register_team_for_stage(team, stage)

    assert exc.value.machine_code == "ALREADY_REGISTERED"


def test_a_team_cannot_enter_a_stage_of_another_edition(team, teamed):
    other = StageFactory(edition=CurrentEditionFactory(competition=teamed, is_current=False))

    with pytest.raises(DomainError) as exc:
        register_team_for_stage(team, other)

    assert exc.value.machine_code == "STAGE_EDITION_MISMATCH"


def test_an_entry_with_two_owners_is_refused_by_the_database(team, teamed):
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)
    participant = ParticipantFactory(competition=teamed)

    with pytest.raises(IntegrityError, match="competitions_stageentry_single_owner"), transaction.atomic():
        StageEntry.objects.create(stage=stage, participant=participant, team=team)


def test_an_entry_without_an_owner_is_refused_by_the_database(team):
    """Jedyny powód, dla którego ``participant`` wolno było znullować – i jego cena."""
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)

    with pytest.raises(IntegrityError, match="competitions_stageentry_single_owner"), transaction.atomic():
        StageEntry.objects.create(stage=stage)


def test_entry_owner_raises_instead_of_returning_none(team):
    """Przeoczone miejsce ma być głośne w pierwszym konkursie drużynowym, a nie ciche w wynikach."""
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)

    with pytest.raises(AttributeError, match="nie ma właściciela"):
        entry_owner(StageEntry(stage=stage))


def test_the_second_team_entry_for_one_stage_is_refused_by_the_database(team):
    stage = StageFactory(edition=team.edition, kind=StageKind.ELIM)
    register_team_for_stage(team, stage)

    with pytest.raises(IntegrityError, match="competitions_stageentry_unique_team"), transaction.atomic():
        StageEntry.objects.create(stage=stage, team=team)


# --- ``ROUND`` na liście rodzajów etapu: za flagą ``process_editor`` (§ 0.1) -----------------------

#: Lista rodzajów, którą koordynator Olimpiady Kwantowej widział **przed** etapem 2 – dosłownie,
#: razem z etykietami i kolejnością. Napisana literałem z rozmysłu: gdyby brała się z ``StageKind``,
#: rozszerzenie wyliczenia przeszłoby przez ten test niezauważone.
KINDS_BEFORE_STAGE_TWO = [
    ("ELIM", "Eliminacje"),
    ("DISTRICT", "Wojewódzki"),
    ("FINAL", "Finał"),
    ("TRAINING", "Trening"),
]


def test_competition_one_sees_exactly_todays_stage_kinds(competition):
    """Konkurs bez ani jednej flagi nie dostaje „Rundy” w oknie dodawania etapu."""
    edition = CurrentEditionFactory(competition=competition)

    assert missing_stage_kinds(edition) == KINDS_BEFORE_STAGE_TWO


def test_a_taken_kind_still_drops_off_the_list(competition):
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(edition=edition, kind=StageKind.ELIM)

    assert missing_stage_kinds(edition) == KINDS_BEFORE_STAGE_TWO[1:]


def test_the_process_editor_adds_the_round(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "process_editor": True}
    competition.save(update_fields=["feature_flags"])
    edition = CurrentEditionFactory(competition=competition)

    assert missing_stage_kinds(edition) == [*KINDS_BEFORE_STAGE_TWO, ("ROUND", "Runda")]


def test_the_round_stays_on_the_list_after_the_first_round_exists(competition):
    """Więz rodzaju jest dla rundy zawieszony, więc druga i piąta muszą dać się dołożyć."""
    competition.feature_flags = {**(competition.feature_flags or {}), "process_editor": True}
    competition.save(update_fields=["feature_flags"])
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(edition=edition, kind=StageKind.ROUND, name="Runda 1")

    assert ("ROUND", "Runda") in missing_stage_kinds(edition)
