"""Ekrany „Drużyny” ``/coordinator/teams/`` i karta drużyny (etap 2, wydanie J, T34).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest **wyłączony** w konkursie z domyślnymi przełącznikami, a adres daje wtedy 404, a nie
  403: Olimpiada Kwantowa po wdrożeniu ma mieć adresy dokładnie takie, jak przed nim (§ 2.1),
- uczestnik dostaje 403 **niezależnie** od stanu flagi – odpowiedź nie zdradza konfiguracji,
- drużyna dostaje kod publiczny z prefiksem konkursu, a jej wpis do etapu nie ma uczestnika
  (więz ``competitions_stageentry_single_owner``),
- drużyny sąsiada nie widać nawet po identyfikatorze (404 z zawężonego querysetu, § 3.6),
- odmowa serwisu wraca z **własnym kodem** domenowym, a nie jako 302 z komunikatem.

Adresy są w ``apps/web/urls.py``: montaż wydania J rozwinął tam ``urls_scoring.urlpatterns``,
więc testy chodzą po mapie produkcyjnej. Ścieżki się nie zmieniły.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.models import StageEntry, Team, TeamMember
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

FEATURE = "team_entries"
LIST_URL = "/coordinator/teams/"


def detail_url(team) -> str:
    return f"/coordinator/teams/{team.pk}/"


def enable(competition):
    """Włącza ekran tą samą drogą, którą zrobi to operator platformy – zapisem do ``feature_flags``."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def edition(competition):
    return CurrentEditionFactory(competition=competition)


@pytest.fixture
def coordinator_client(client_for, competition, edition):
    """Zalogowany koordynator tego konkursu, pod jego domeną, z **włączonym** ekranem."""
    enable(competition)
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def team(competition, edition) -> Team:
    return Team.objects.create(
        competition=competition, edition=edition, name="Kwanty 1", public_code="OLM-TEAM1"
    )


@pytest.fixture
def member(competition):
    return ParticipantFactory(competition=competition)


# --- bramki: flaga i rola ---------------------------------------------------------------------


def test_the_screen_is_a_404_without_the_flag(client_for, competition, edition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert client.get(LIST_URL).status_code == 404


def test_a_participant_gets_403_whatever_the_flag(client_for, competition, edition):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    assert client.get(LIST_URL).status_code == 403
    enable(competition)
    assert client.get(LIST_URL).status_code == 403


def test_a_team_of_another_competition_is_a_404(coordinator_client, other_competition):
    other_edition = CurrentEditionFactory(competition=other_competition)
    foreign = Team.objects.create(
        competition=other_competition, edition=other_edition, name="Obcy", public_code="INN-1"
    )

    assert coordinator_client.get(detail_url(foreign)).status_code == 404


# --- zakładanie drużyny -----------------------------------------------------------------------


def test_a_coordinator_creates_a_team(coordinator_client, competition, edition):
    response = coordinator_client.post(
        LIST_URL, {"name": "Kwanty 2", "school": "LO nr 3", "supervisor_email": ""}
    )

    team = Team.objects.get(name="Kwanty 2")
    assert response.status_code == 302
    assert response["Location"] == detail_url(team)
    assert team.edition == edition
    # Kod publiczny nadaje serwis, tym samym generatorem, co uczestnikom – razem z prefiksem konkursu.
    assert team.public_code.startswith(competition.public_code_prefix)


def test_a_repeated_name_is_a_conflict(coordinator_client, team):
    response = coordinator_client.post(LIST_URL, {"name": team.name, "school": "", "supervisor_email": ""})

    assert response.status_code == 409
    assert Team.objects.filter(name=team.name).count() == 1


def test_an_empty_name_is_a_form_error(coordinator_client):
    response = coordinator_client.post(LIST_URL, {"name": "", "school": "", "supervisor_email": ""})

    assert response.status_code == 400
    assert Team.objects.count() == 0


# --- skład -------------------------------------------------------------------------------------


def test_a_member_is_added_by_public_code(coordinator_client, team, member):
    response = coordinator_client.post(
        detail_url(team), {"public_code": member.public_code, "is_captain": "on"}
    )

    assert response.status_code == 302
    assert TeamMember.objects.get(team=team, participant=member).is_captain is True


def test_an_unknown_code_is_a_form_error(coordinator_client, team):
    response = coordinator_client.post(detail_url(team), {"public_code": "OLM-NIEMA"})

    assert response.status_code == 400
    assert "Nie ma uczestnika o tym kodzie" in response.content.decode()
    assert TeamMember.objects.count() == 0


def test_the_second_captain_is_refused(coordinator_client, team, member, competition):
    TeamMember.objects.create(team=team, participant=member, is_captain=True)
    other = ParticipantFactory(competition=competition)

    response = coordinator_client.post(
        detail_url(team), {"public_code": other.public_code, "is_captain": "on"}
    )

    assert response.status_code == 409
    assert TeamMember.objects.filter(team=team).count() == 1


def test_the_captain_is_handed_over(coordinator_client, team, member, competition):
    TeamMember.objects.create(team=team, participant=member, is_captain=True)
    other = ParticipantFactory(competition=competition)
    TeamMember.objects.create(team=team, participant=other, is_captain=False)

    response = coordinator_client.post(f"{detail_url(team)}captain/", {"public_code": other.public_code})

    assert response.status_code == 302
    assert TeamMember.objects.get(team=team, participant=other).is_captain is True
    assert TeamMember.objects.get(team=team, participant=member).is_captain is False


def test_a_member_is_removed(coordinator_client, team, member):
    TeamMember.objects.create(team=team, participant=member)

    response = coordinator_client.post(
        f"{detail_url(team)}members/remove/", {"public_code": member.public_code}
    )

    assert response.status_code == 302
    assert TeamMember.objects.filter(team=team).count() == 0


def test_removing_someone_outside_the_squad_is_refused(coordinator_client, team, member):
    response = coordinator_client.post(
        f"{detail_url(team)}members/remove/", {"public_code": member.public_code}
    )

    assert response.status_code == 404


# --- wpis do etapu ------------------------------------------------------------------------------


def test_a_team_is_registered_for_a_stage(coordinator_client, competition, edition, team):
    stage = StageFactory(competition=competition, edition=edition)

    response = coordinator_client.post(f"{detail_url(team)}stage/", {"stage": stage.pk})

    entry = StageEntry.objects.get(team=team)
    assert response.status_code == 302
    # Wpis ma właściciela dokładnie jednego – to jest cały powód, dla którego ``participant``
    # wolno było znullować.
    assert entry.participant_id is None
    assert entry.stage == stage


def test_a_stage_already_taken_is_off_the_list(coordinator_client, competition, edition, team):
    stage = StageFactory(competition=competition, edition=edition)
    StageEntry.objects.create(stage=stage, team=team, participant=None)

    response = coordinator_client.post(f"{detail_url(team)}stage/", {"stage": stage.pk})

    # Etap, do którego drużyna już należy, znika z listy wyboru – więc to jest błąd **formularza**,
    # a nie odmowa serwisu: ekran nie proponuje czynności, której serwis by nie przyjął.
    assert response.status_code == 400
    assert StageEntry.objects.filter(team=team).count() == 1


# --- karta „Moja drużyna” na pulpicie uczestnika (§ 2.3) ---------------------------------------


def test_the_participant_dashboard_says_nothing_about_teams_without_the_flag(
    client_for, competition, edition, team, member
):
    """Pulpit Olimpiady Kwantowej renderuje się dokładnie tak, jak przed wydaniem J."""
    TeamMember.objects.create(team=team, participant=member)
    client = client_for(competition)
    client.force_login(member.user)

    assert "Moja drużyna" not in client.get("/me/").content.decode()


def test_the_participant_sees_the_squad_by_public_codes(client_for, competition, edition, team, member):
    enable(competition)
    TeamMember.objects.create(team=team, participant=member, is_captain=True)
    client = client_for(competition)
    client.force_login(member.user)

    body = client.get("/me/").content.decode()

    assert "Moja drużyna" in body
    assert team.name in body
    assert team.public_code in body
    # Pseudonim, nie nazwisko: pulpit nie jest listą osób.
    assert member.public_code in body


def test_a_participant_without_a_team_gets_no_card(client_for, competition, edition, member):
    enable(competition)
    client = client_for(competition)
    client.force_login(member.user)

    assert "Moja drużyna" not in client.get("/me/").content.decode()
