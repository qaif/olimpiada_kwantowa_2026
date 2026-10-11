"""Tokeny i zakres per rola – w tym izolacja opiekuna drużyny pokojem LiveKit (PROC-01 § 3)."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from django.utils import timezone

from apps.accounts.models import CommitteeStatus
from apps.core.api import DomainError
from apps.proctoring import services
from apps.proctoring.models import ProctorAssignment, ProctoringSession, ProctorKind

from .conftest import make_student, ready_session
from .fake_livekit import API_SECRET

pytestmark = pytest.mark.django_db


def claims(token: str) -> dict:
    return jwt.decode(token, API_SECRET, algorithms=["HS256"])


def test_student_token_publishes_camera_and_subscribes_to_nothing(
    proctoring_on, fake_livekit, config, stage, student
):
    session = ready_session(stage, student, started=False)
    data = services.student_token(session, config, user=student.user)
    video = claims(data["token"])["video"]
    assert video["room"] == config.room_name("m")
    assert video["canPublish"] is True
    assert video["canPublishSources"] == ["camera"]
    assert video["canSubscribe"] is False
    assert video["canPublishData"] is False
    assert video["canUpdateOwnMetadata"] is False
    # Nazwa pusta, identity – pseudonim: inni uczniowie w pokoju nie widzą nazwiska.
    assert claims(data["token"])["name"] == ""
    assert data["identity"] == services.student_identity(stage, student)
    assert data["identity"].startswith("p-") and len(data["identity"]) == 22  # HMAC, nie ``pk``


def test_student_token_adds_screen_and_microphone_only_when_required(
    proctoring_on, fake_livekit, config, stage, student
):
    config.require_screen_share = True
    config.require_microphone = True
    config.save()
    session = ready_session(stage, student, started=False)
    session.check_result = {"screen": True, "microphone": True}
    video = claims(services.student_token(session, config, user=student.user)["token"])["video"]
    assert video["canPublishSources"] == ["camera", "screen_share", "microphone"]


@pytest.mark.parametrize("missing", ["consent", "check", "photo"])
def test_student_token_requires_every_step(proctoring_on, fake_livekit, config, stage, student, missing):
    config.id_photo = "required"
    config.save()
    session = ready_session(stage, student, started=False)
    session.id_photo_key = "proctoring/x.jpg"
    if missing == "consent":
        services.withdraw_consent(session, user=student.user)
    elif missing == "check":
        session.check_passed_at = None
    else:
        session.id_photo_key = ""
    with pytest.raises(DomainError) as error:
        services.student_token(session, config, user=student.user)
    assert error.value.status_code == 409


def test_student_token_outside_the_window_is_refused(proctoring_on, fake_livekit, config, stage, student):
    session = ready_session(stage, student, started=False)
    later = stage.submission_deadline + timedelta(minutes=1)
    with pytest.raises(DomainError) as error:
        services.student_token(session, config, user=student.user, now=later)
    assert error.value.machine_code == "PROCTORING_OVER"


def test_disqualified_student_gets_no_token(proctoring_on, fake_livekit, config, stage, student):
    from apps.competitions.models import StageEntry, StageEntryStatus

    session = ready_session(stage, student, started=False)
    StageEntry.objects.filter(participant=student).update(status=StageEntryStatus.DISQUALIFIED)
    with pytest.raises(DomainError) as error:
        services.student_token(session, config, user=student.user)
    assert error.value.status_code == 404


def test_window_adapter_from_tz01_is_used(proctoring_on, fake_livekit, config, stage, student, settings):
    settings.PROCTORING_WINDOW_ADAPTER = "apps.proctoring.tests.test_tokens.closed_window"
    session = ready_session(stage, student, started=False)
    with pytest.raises(DomainError) as error:
        services.student_token(session, config, user=student.user)
    assert error.value.machine_code == "PROCTORING_OVER"


def closed_window(stage, participant):
    """Adapter TZ-01 w teście: okno ucznia zamknęło się godzinę temu."""
    now = timezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


def test_proctor_token_is_hidden_and_cannot_publish(proctoring_on, fake_livekit, config, stage, coordinator):
    scope = services.proctor_scope(coordinator, stage)
    token = services.proctor_token(scope, "m")["token"]
    video = claims(token)["video"]
    assert video == {
        "room": config.room_name("m"),
        "roomJoin": True,
        "canPublish": False,
        "canSubscribe": True,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
        "hidden": True,
    }


def test_person_without_role_has_no_scope(proctoring_on, config, stage, student, reviewer):
    assert services.proctor_scope(student.user, stage) is None
    # Recenzent bez przydziału do etapu też nie nadzoruje.
    assert services.proctor_scope(reviewer, stage) is None


def test_committee_member_sees_only_assigned_students(
    proctoring_on, fake_livekit, config, stage, competition, coordinator, reviewer
):
    mine = make_student(competition, stage, first="Ela")
    other = make_student(competition, stage, first="Ola")
    services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    services.ensure_sessions(stage)
    assignment = ProctorAssignment.objects.get(user=reviewer)
    mine_session = ProctoringSession.objects.get(participant=mine)
    services.reassign(stage, mine_session.pk, assignment.pk, coordinator)
    mine_session.refresh_from_db()
    # Pokój na przydział: uczeń członka komisji nadaje do pokoju „a<przydział>”.
    assert mine_session.group == f"a{assignment.pk}"
    scope = services.proctor_scope(reviewer, stage)
    assert scope.allowed_groups() == [f"a{assignment.pk}"]
    ids = {item["id"] for item in services.roster(scope, group=f"a{assignment.pk}", size=24)["items"]}
    assert ids == {mine_session.pk}
    token = services.proctor_token(scope, f"a{assignment.pk}")["token"]
    assert claims(token)["video"]["room"] == config.room_name(f"a{assignment.pk}")
    # Pokój uczniów bez przydziału (i innych nadzorujących) – poza zasięgiem tokenu.
    with pytest.raises(DomainError):
        services.proctor_token(scope, "m")
    other_session = ProctoringSession.objects.get(participant=other)
    with pytest.raises(DomainError) as error:
        services.session_in_scope(scope, other_session.pk)
    assert error.value.status_code == 404


def test_reassign_moves_a_connected_student_to_the_new_room(
    proctoring_on, fake_livekit, config, stage, student, coordinator, reviewer
):
    session = ready_session(stage, student)
    ProctoringSession.objects.filter(pk=session.pk).update(connected=True)
    fake_livekit.publish(config.room_name("m"), session.identity, "CAMERA")
    assignment = services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    services.reassign(stage, session.pk, assignment.pk, coordinator)
    assert fake_livekit.payload("RemoveParticipant") == {
        "room": config.room_name("m"),
        "identity": session.identity,
    }


def test_suspended_committee_member_loses_scope_immediately(
    proctoring_on, config, stage, coordinator, reviewer
):
    from apps.accounts.models import CommitteeMember

    services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    CommitteeMember.objects.filter(user=reviewer).update(status=CommitteeStatus.SUSPENDED)
    fresh = type(reviewer).objects.get(pk=reviewer.pk)  # jak w następnym żądaniu – bez profilu w pamięci
    assert services.proctor_scope(fresh, stage) is None


# --- opiekun drużyny ------------------------------------------------------------------------------


@pytest.fixture
def two_delegations(proctoring_on, config, stage, competition, coordinator, leader, delegations_map):
    """Dwie delegacje: 7 (opiekun ``leader``) i 9; po jednym uczniu w każdej."""
    ours = make_student(competition, stage, first="Jan")
    theirs = make_student(competition, stage, first="Hans")
    delegations_map.students.update({ours.pk: 7, theirs.pk: 9})
    delegations_map.leaders[leader.pk] = 7
    services.add_assignment(stage, coordinator, leader, ProctorKind.LEADER)
    services.ensure_sessions(stage)
    return ours, theirs


def test_leader_token_opens_only_own_delegation_room(two_delegations, fake_livekit, config, stage, leader):
    scope = services.proctor_scope(leader, stage)
    assert scope.kind == ProctorKind.LEADER
    assert scope.allowed_groups() == ["d7"]
    token = services.proctor_token(scope, "d7")["token"]
    assert claims(token)["video"]["room"] == config.room_name("d7")
    for group in ("d9", "m"):
        with pytest.raises(DomainError) as error:
            services.proctor_token(scope, group)
        assert error.value.status_code == 404


def test_leader_roster_and_actions_stop_at_own_delegation(two_delegations, fake_livekit, stage, leader):
    ours, theirs = two_delegations
    scope = services.proctor_scope(leader, stage)
    roster = services.roster(scope, group="d7", size=24)
    assert [item["id"] for item in roster["items"]] == [ProctoringSession.objects.get(participant=ours).pk]
    with pytest.raises(DomainError):
        services.roster(scope, group="d9")
    theirs_session = ProctoringSession.objects.get(participant=theirs)
    with pytest.raises(DomainError) as error:
        services.session_in_scope(scope, theirs_session.pk)
    assert error.value.status_code == 404


def test_leader_assigned_to_foreign_student_still_cannot_see_them(two_delegations, stage, leader):
    """Pomyłka koordynatora (przydział ucznia innego kraju) nie otwiera go opiekunowi."""
    _ours, theirs = two_delegations
    assignment = ProctorAssignment.objects.get(user=leader)
    ProctoringSession.objects.filter(participant=theirs).update(proctor=assignment)
    scope = services.proctor_scope(leader, stage)
    assert theirs.pk not in {s.participant_id for s in scope.sessions()}


def test_reassigning_foreign_student_to_leader_is_refused(two_delegations, stage, coordinator, leader):
    _ours, theirs = two_delegations
    assignment = ProctorAssignment.objects.get(user=leader)
    session = ProctoringSession.objects.get(participant=theirs)
    with pytest.raises(DomainError):
        services.reassign(stage, session.pk, assignment.pk, coordinator)


def test_removed_leader_loses_scope(two_delegations, stage, leader, delegations_map):
    delegations_map.leaders.pop(leader.pk)
    assert services.proctor_scope(leader, stage) is None


def test_leader_moved_to_another_delegation_loses_scope(two_delegations, stage, leader, delegations_map):
    delegations_map.leaders[leader.pk] = 9
    assert services.proctor_scope(leader, stage) is None


def test_student_room_is_their_delegation_room(two_delegations, fake_livekit, config, stage):
    ours, theirs = two_delegations
    session = ready_session(stage, theirs, started=False)
    data = services.student_token(session, config, user=theirs.user)
    assert claims(data["token"])["video"]["room"] == config.room_name("d9")


def test_distribute_sends_delegation_students_to_their_leader(
    two_delegations, stage, coordinator, reviewer, leader
):
    ours, theirs = two_delegations
    services.add_assignment(stage, coordinator, reviewer, ProctorKind.COMMITTEE)
    services.distribute(stage, coordinator)
    assert ProctoringSession.objects.get(participant=ours).proctor.user == leader
    # Delegacja bez przydzielonego opiekuna – do komisji.
    assert ProctoringSession.objects.get(participant=theirs).proctor.user == reviewer


def test_leader_endpoint_returns_404_for_foreign_student(
    two_delegations, fake_livekit, logged, stage, leader
):
    _ours, theirs = two_delegations
    session = ProctoringSession.objects.get(participant=theirs)
    response = logged(leader).post(f"/proctoring/{stage.pk}/s/{session.pk}/action/", {"action": "present"})
    assert response.status_code == 404
    response = logged(leader).post(f"/proctoring/{stage.pk}/token/", {"group": "d9"})
    assert response.status_code == 404
