"""Widoczność wpisu drużynowego w ``StageEntryQuerySet.for_user`` (§ 1.2.3).

Ten filtr jest jedyną gwarancją, że uczestnik widzi wyłącznie swoje wpisy, więc dołożona do niego
gałąź jest tu sprawdzana z obu stron: kto ma widzieć wpis swojej drużyny i **kto nie ma widzieć
niczego więcej niż dotąd**.

Najważniejsza asercja jest ostatnia i nie dotyczy uprawnień, tylko kosztu: bez flagi
``team_entries`` zapytanie ma nie mieć drugiego złączenia. Konkurs bez drużyn ma płacić za nie
zero – i ma wychodzić z tego querysetu dokładnie tą drogą, którą wychodził przed etapem 2 (§ 0.1).
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntry, Team, TeamMember

from .factories import CurrentEditionFactory, StageEntryFactory, StageFactory

pytestmark = pytest.mark.django_db

FEATURE = "team_entries"


def enable_teams(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def stage(competition):
    return StageFactory(competition=competition, edition=CurrentEditionFactory(competition=competition))


@pytest.fixture
def member(competition):
    return ParticipantFactory(competition=competition)


@pytest.fixture
def team_entry(competition, stage, member) -> StageEntry:
    """Wpis, którego właścicielem jest drużyna z jednym zawodnikiem w składzie."""
    team = Team.objects.create(
        competition=competition, edition=stage.edition, name="Kwanty 1", public_code="OLM-TEAM1"
    )
    TeamMember.objects.create(team=team, participant=member, is_captain=True)
    return StageEntry.objects.create(stage=stage, team=team, participant=None)


def test_without_the_flag_a_team_mate_sees_nothing(competition, member, team_entry):
    """Konkurs #1: wpis drużynowy nie istnieje, a gdyby istniał, filtr zachowuje się jak dotąd."""
    assert list(StageEntry.objects.for_user(member.user, competition)) == []


def test_with_the_flag_a_team_mate_sees_the_team_entry(competition, member, team_entry):
    enable_teams(competition)

    assert list(StageEntry.objects.for_user(member.user, competition)) == [team_entry]


def test_someone_outside_the_team_sees_nothing(competition, stage, team_entry):
    enable_teams(competition)
    stranger = ParticipantFactory(competition=competition)

    assert list(StageEntry.objects.for_user(stranger.user, competition)) == []


def test_the_participants_own_entry_is_still_visible(competition, stage, member, team_entry):
    """Gałąź drużynowa **dokłada** wpisy, a nie podmienia – własny wpis zostaje na liście."""
    enable_teams(competition)
    own = StageEntryFactory(competition=competition, stage=stage, participant=member)

    visible = set(StageEntry.objects.for_user(member.user, competition))

    assert visible == {own, team_entry}


def test_a_team_entry_of_another_edition_stage_is_not_smuggled_in(competition, member, team_entry):
    """Zawężenie do konkursu zostaje pierwsze: gałąź drużynowa działa **w** nim, a nie obok niego."""
    enable_teams(competition)
    other_stage = StageFactory(
        competition=competition, edition=CurrentEditionFactory(competition=competition, is_current=False)
    )
    foreign = StageEntryFactory(competition=competition, stage=other_stage)

    assert foreign not in set(StageEntry.objects.for_user(member.user, competition))


def test_without_the_flag_the_query_has_no_team_join(competition, member):
    """Koszt, nie uprawnienie: bez flagi w zapytaniu nie ma ani śladu tabeli składu."""
    without = str(StageEntry.objects.for_user(member.user, competition).query)
    enable_teams(competition)
    with_flag = str(StageEntry.objects.for_user(member.user, competition).query)

    assert "teammember" not in without.lower()
    assert "teammember" in with_flag.lower()
