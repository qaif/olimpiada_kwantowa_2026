"""Reguły webinarów: odbiorcy, role, okno wejścia, tokeny, głos, nagrania, webhooki, listy."""

from __future__ import annotations

import json
from datetime import timedelta

import jwt
import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.models import StageEntryStatus, StageKind, Team, TeamMember
from apps.competitions.tests.factories import StageEntryFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership
from apps.webinars import services
from apps.webinars.models import (
    Audience,
    RecordingStatus,
    Webinar,
    WebinarAttendee,
    WebinarNotificationSettings,
    WebinarRecording,
)
from apps.webinars.tasks import remind_webinars, send_webinar_invitation

from .conftest import make_webinar
from .fake_livekit import API_SECRET, MemoryRecordingStorage

pytestmark = pytest.mark.django_db


def viewer(user, competition):
    return services.Viewer.of(user, competition)


def claims(token: str) -> dict:
    return jwt.decode(token, API_SECRET, algorithms=["HS256"])


def started(webinar, user):
    services.start(webinar, user)
    webinar.refresh_from_db()
    return webinar


# --- przełączniki --------------------------------------------------------------------------------


def test_flag_is_off_by_default(competition, fake_livekit):
    assert competition.has_feature("webinars") is False
    assert not services.enabled(competition)
    assert not services.available(competition)


def test_flag_without_server_is_enabled_but_not_available(webinars_on, settings):
    settings.LIVEKIT_URL = ""
    assert services.enabled(webinars_on)
    assert not services.available(webinars_on)


# --- odbiorcy ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("audience", "expected"),
    [
        (Audience.COMPETITION, True),
        (Audience.EDITION, True),
        (Audience.STAGE, True),
        (Audience.COMMITTEE, False),
        (Audience.CAPTAINS, False),
    ],
)
def test_participant_audiences(webinars_on, fake_livekit, participant, stage, audience, expected):
    webinar = make_webinar(
        webinars_on, audience=audience, stage=stage if audience == Audience.STAGE else None
    )
    person = viewer(participant.user, webinars_on)

    assert person.in_audience(webinar) is expected
    assert (person.role(webinar) == services.ROLE_VIEWER) is expected


def test_stage_audience_excludes_other_stage_and_disqualified(
    webinars_on, fake_livekit, participant, stage, edition
):
    other_stage = StageFactory(edition=edition, competition=webinars_on, kind=StageKind.DISTRICT)
    assert not viewer(participant.user, webinars_on).in_audience(
        make_webinar(webinars_on, audience=Audience.STAGE, stage=other_stage)
    )
    on_stage = make_webinar(webinars_on, audience=Audience.STAGE, stage=stage)
    participant.stage_entries.update(status=StageEntryStatus.DISQUALIFIED)
    assert not viewer(participant.user, webinars_on).in_audience(on_stage)


def test_committee_audience_and_include_committee(webinars_on, fake_livekit, reviewer, participant):
    committee_only = make_webinar(webinars_on, audience=Audience.COMMITTEE)
    for_participants = make_webinar(webinars_on, audience=Audience.COMPETITION)
    both = make_webinar(webinars_on, audience=Audience.COMPETITION, include_committee=True)
    member = viewer(reviewer.user, webinars_on)

    assert member.role(committee_only) == services.ROLE_VIEWER
    assert member.role(for_participants) is None
    assert member.role(both) == services.ROLE_VIEWER
    assert viewer(participant.user, webinars_on).role(committee_only) is None


def test_captains_only_with_team_flag(webinars_on, fake_livekit, participant, edition):
    webinar = make_webinar(webinars_on, audience=Audience.CAPTAINS)
    team = Team.objects.create(competition=webinars_on, edition=edition, name="Kwanty")
    TeamMember.objects.create(team=team, participant=participant, is_captain=True)
    assert not viewer(participant.user, webinars_on).in_audience(webinar)

    webinars_on.feature_flags = {**webinars_on.feature_flags, "team_entries": True}
    webinars_on.save(update_fields=["feature_flags"])
    assert viewer(participant.user, webinars_on).in_audience(webinar)


def test_cancelled_webinar_is_invisible_to_audience_not_to_presenters(
    webinars_on, fake_livekit, participant, coordinator
):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION, cancelled_at=timezone.now())

    assert viewer(participant.user, webinars_on).role(webinar) is None
    assert viewer(coordinator, webinars_on).role(webinar) == services.ROLE_PRESENTER


def test_account_without_role_sees_nothing(webinars_on, fake_livekit):
    person = viewer(UserFactory(), webinars_on)
    assert not services.has_any_role(person)
    assert person.role(make_webinar(webinars_on, audience=Audience.COMPETITION)) is None


def test_webinar_of_another_competition_is_never_visible(
    webinars_on, other_competition, fake_livekit, participant
):
    foreign = make_webinar(other_competition, audience=Audience.COMPETITION)
    person = viewer(participant.user, webinars_on)

    assert person.role(foreign) is None
    assert foreign not in services.webinars_for(person)[0]
    with pytest.raises(DomainError) as error:
        services.join_token(foreign, person)
    assert error.value.status_code == 404


def test_presenter_mapping(webinars_on, fake_livekit, coordinator, reviewer, participant):
    lecturer = ActiveReviewerFactory(competition=webinars_on)
    grant_membership(lecturer.user, webinars_on, CompetitionRole.REVIEWER)
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION)
    webinar.co_moderators.set([lecturer.user])

    assert viewer(coordinator, webinars_on).role(webinar) == services.ROLE_PRESENTER
    assert viewer(lecturer.user, webinars_on).role(webinar) == services.ROLE_PRESENTER
    assert viewer(reviewer.user, webinars_on).role(webinar) is None
    assert viewer(participant.user, webinars_on).role(webinar) == services.ROLE_VIEWER


def test_co_moderator_loses_rights_with_the_role(webinars_on, fake_livekit):
    lecturer = ActiveReviewerFactory(competition=webinars_on)
    grant_membership(lecturer.user, webinars_on, CompetitionRole.REVIEWER)
    webinar = make_webinar(webinars_on, audience=Audience.COMMITTEE)
    webinar.co_moderators.set([lecturer.user])
    lecturer.status = "SUSPENDED"
    lecturer.save(update_fields=["status"])

    assert viewer(lecturer.user, webinars_on).role(webinar) is None


def test_create_refuses_participant_as_co_moderator_and_foreign_stage(
    webinars_on, fake_livekit, coordinator, participant, other_competition
):
    data = {
        "title": "x",
        "starts_at": timezone.now(),
        "duration_minutes": 60,
        "audience": Audience.COMPETITION,
    }
    with pytest.raises(DomainError):
        services.create_webinar(webinars_on, coordinator, dict(data), [participant.user])
    foreign_stage = StageFactory(competition=other_competition)
    with pytest.raises(DomainError):
        services.create_webinar(
            webinars_on, coordinator, {**data, "audience": Audience.STAGE, "stage": foreign_stage}, []
        )


# --- tokeny wejścia ------------------------------------------------------------------------------


def test_presenter_token_starts_the_webinar(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on)

    data = services.join_token(webinar, viewer(coordinator, webinars_on))

    grant = claims(data["token"])["video"]
    assert data["url"] == "wss://live.example.test"
    assert data["role"] == services.ROLE_PRESENTER
    assert grant["room"] == webinar.room_name
    assert grant["canPublish"] is True and grant["roomAdmin"] is True
    assert claims(data["token"])["sub"] == services.pseudonym(coordinator)
    assert claims(data["token"])["name"] == "Ola K."
    webinar.refresh_from_db()
    assert webinar.started_at is not None
    attendee = WebinarAttendee.objects.get(webinar=webinar)
    assert attendee.user == coordinator and attendee.role == "presenter"
    assert set(AuditLog.objects.filter(target_id=str(webinar.pk)).values_list("action", flat=True)) >= {
        "webinar.started",
        "webinar.joined",
    }


def test_viewer_token_requires_window_and_start(webinars_on, fake_livekit, participant, coordinator):
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION, starts_in_minutes=120)
    person = viewer(participant.user, webinars_on)
    with pytest.raises(DomainError) as early:
        services.join_token(webinar, person)
    assert early.value.machine_code == "WEBINAR_NOT_YET"

    webinar.starts_at = timezone.now() + timedelta(minutes=5)
    webinar.save()
    with pytest.raises(DomainError) as not_started:
        services.join_token(webinar, person)
    assert not_started.value.machine_code == "WEBINAR_NOT_RUNNING"

    started(webinar, coordinator)
    data = services.join_token(webinar, person)
    grant = claims(data["token"])["video"]
    assert grant["canPublish"] is False
    assert grant["canSubscribe"] is True and grant["canPublishData"] is True
    assert "roomAdmin" not in grant
    assert claims(data["token"])["name"] == "Ala N."


def test_no_token_for_non_audience(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMMITTEE), coordinator)
    with pytest.raises(DomainError) as error:
        services.join_token(webinar, viewer(participant.user, webinars_on))
    assert error.value.status_code == 404


def test_after_end_nobody_gets_a_token(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    services.end(webinar, coordinator)

    assert "DeleteRoom" in fake_livekit.names()
    with pytest.raises(DomainError) as over:
        services.join_token(webinar, viewer(participant.user, webinars_on))
    assert over.value.machine_code == "WEBINAR_OVER"


def test_window_closes_after_grace(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on, starts_in_minutes=-(60 + 31))
    assert services.window_state(webinar) == "closed"
    with pytest.raises(DomainError):
        services.start(webinar, coordinator)


def test_guest_token_only_with_public_link(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on), coordinator)
    assert services.webinar_for_key(webinars_on, webinar.public_key) is None
    webinar.public_link = True
    webinar.save()
    assert services.webinar_for_key(webinars_on, webinar.public_key) == webinar

    identity = services.new_guest_identity()
    data = services.guest_token(webinar, identity=identity, name="Gość Zewnętrzny")
    grant = claims(data["token"])["video"]
    assert grant["canPublish"] is False
    assert claims(data["token"])["sub"] == identity
    assert "Gość" not in json.dumps(AuditLog.objects.order_by("-id").first().diff)
    with pytest.raises(DomainError):
        services.guest_token(webinar, identity="u-zly", name="x")


# --- głos i usuwanie ------------------------------------------------------------------------------


def test_promote_viewer_to_speaker_and_back(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    data = services.join_token(webinar, viewer(participant.user, webinars_on))
    fake_livekit.join(webinar.room_name, data["identity"])

    services.set_speaker(webinar, coordinator, data["identity"], True)
    assert fake_livekit.rooms[webinar.room_name][data["identity"]]["can_publish"] is True
    services.set_speaker(webinar, coordinator, data["identity"], False)
    assert fake_livekit.rooms[webinar.room_name][data["identity"]]["can_publish"] is False
    actions = list(AuditLog.objects.filter(target_id=str(webinar.pk)).values_list("action", flat=True))
    assert "webinar.speaker_granted" in actions and "webinar.speaker_revoked" in actions


def test_promote_refuses_identity_without_token_for_this_webinar(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on), coordinator)
    with pytest.raises(DomainError) as error:
        services.set_speaker(webinar, coordinator, "u-0123456789abcdef0123", True)
    assert error.value.status_code == 404
    assert "UpdateParticipant" not in fake_livekit.names()


def test_remove_from_room(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    identity = services.join_token(webinar, viewer(participant.user, webinars_on))["identity"]
    fake_livekit.join(webinar.room_name, identity)

    services.remove_from_room(webinar, coordinator, identity)
    assert identity not in fake_livekit.rooms[webinar.room_name]


# --- nagrania -------------------------------------------------------------------------------------


def test_recording_flow_with_egress_and_webhook(webinars_on, fake_livekit, coordinator, participant):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION, record=True), coordinator)
    recording = services.start_recording(webinar, coordinator)
    assert recording.status == RecordingStatus.ACTIVE
    assert recording.storage_key.startswith(f"webinars/{webinars_on.slug}/{webinar.room_key}/")
    with pytest.raises(DomainError):
        services.start_recording(webinar, coordinator)

    services.stop_recording(webinar, coordinator)
    assert fake_livekit.egresses[recording.egress_id]["active"] is False
    services.handle_webhook(
        {
            "event": "egress_ended",
            "id": "EV_egress",
            "createdAt": int(timezone.now().timestamp()),
            "egressInfo": {
                "egressId": recording.egress_id,
                "status": "EGRESS_COMPLETE",
                "fileResults": [
                    {"filename": recording.storage_key, "size": "1048576", "duration": "125000000000"}
                ],
            },
        }
    )
    recording.refresh_from_db()
    assert recording.status == RecordingStatus.COMPLETE
    assert recording.size == 1048576 and recording.duration_seconds == 125

    person = viewer(participant.user, webinars_on)
    with pytest.raises(DomainError):
        services.recording_url_for(webinar, person, recording.pk)
    services.set_recording_published(webinar, recording.pk, True, coordinator)
    url = services.recording_url_for(webinar, person, recording.pk)
    assert url.startswith("https://s3.example.test/submissions/webinars/")
    assert MemoryRecordingStorage.signed == [recording.storage_key]

    services.delete_recording(webinar, recording.pk, coordinator)
    assert MemoryRecordingStorage.deleted == [recording.storage_key]
    assert not WebinarRecording.objects.exists()


def test_recording_requires_permission_flag_and_live_webinar(webinars_on, fake_livekit, coordinator):
    webinar = make_webinar(webinars_on, record=True)
    with pytest.raises(DomainError):
        services.start_recording(webinar, coordinator)
    started(webinar, coordinator)
    webinar.record = False
    webinar.save()
    with pytest.raises(DomainError):
        services.start_recording(webinar, coordinator)


def test_recording_of_another_webinar_cannot_be_touched(webinars_on, fake_livekit, coordinator):
    mine = make_webinar(webinars_on, record=True)
    other = make_webinar(webinars_on, record=True)
    foreign = WebinarRecording.objects.create(
        webinar=other, egress_id="EG_x", storage_key="webinars/x/y/z.mp4", status=RecordingStatus.COMPLETE
    )
    with pytest.raises(DomainError) as error:
        services.delete_recording(mine, foreign.pk, coordinator)
    assert error.value.status_code == 404
    assert MemoryRecordingStorage.deleted == []


def test_webhook_cannot_point_recording_outside_its_prefix(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on, record=True), coordinator)
    recording = services.start_recording(webinar, coordinator)
    services.handle_webhook(
        {
            "event": "egress_ended",
            "id": "EV_evil",
            "egressInfo": {
                "egressId": recording.egress_id,
                "fileResults": [{"filename": "submissions-of-others.pdf"}],
            },
        }
    )
    recording.refresh_from_db()
    assert recording.storage_key.startswith(f"webinars/{webinars_on.slug}/{webinar.room_key}/")


# --- transmisja RTMP -----------------------------------------------------------------------------


def test_stream_key_is_not_stored(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on), coordinator)
    services.start_stream(webinar, coordinator, "abcd-efgh-ijkl-mnop")

    payload = fake_livekit.payload("StartRoomCompositeEgress")
    assert payload["stream_outputs"][0]["urls"] == ["rtmps://a.rtmps.youtube.com/live2/abcd-efgh-ijkl-mnop"]
    webinar.refresh_from_db()
    assert webinar.stream_egress_id
    stored = json.dumps(
        [
            list(Webinar.objects.filter(pk=webinar.pk).values())[0],
            list(AuditLog.objects.values_list("diff", flat=True)),
        ],
        default=str,
    )
    assert "abcd-efgh" not in stored
    services.stop_stream(webinar, coordinator)
    webinar.refresh_from_db()
    assert webinar.stream_egress_id == ""
    with pytest.raises(DomainError):
        services.rtmp_target("http://evil.example/x")


# --- webhooki: stan pokoju i obecność ---------------------------------------------------------------


def event(name, webinar, **extra):
    return {
        "event": name,
        "id": f"EV_{name}_{extra.pop('suffix', '')}",
        "createdAt": int(extra.pop("at", timezone.now()).timestamp()),
        "room": {"name": webinar.room_name},
        **extra,
    }


def test_webhooks_build_live_status_and_attendance(webinars_on, fake_livekit, participant, coordinator):
    webinar = started(make_webinar(webinars_on, audience=Audience.COMPETITION), coordinator)
    identity = services.join_token(webinar, viewer(participant.user, webinars_on))["identity"]
    now = timezone.now()

    assert services.handle_webhook(event("room_started", webinar, at=now - timedelta(minutes=10))) == "ok"
    services.handle_webhook(
        event(
            "participant_joined",
            webinar,
            at=now - timedelta(minutes=9),
            participant={"identity": identity, "name": "Ala N."},
        )
    )
    webinar.refresh_from_db()
    assert webinar.is_live
    assert services.present_count(webinar) == 1
    services.handle_webhook(
        event("participant_left", webinar, at=now - timedelta(minutes=4), participant={"identity": identity})
    )
    attendee = WebinarAttendee.objects.get(webinar=webinar, identity=identity)
    assert attendee.user == participant.user
    assert attendee.seconds == 300
    services.handle_webhook(event("room_finished", webinar, at=now))
    webinar.refresh_from_db()
    assert not webinar.is_live


def test_webhook_replay_and_stale_events_change_nothing(webinars_on, fake_livekit, coordinator):
    webinar = started(make_webinar(webinars_on), coordinator)
    first = event("room_started", webinar, suffix="1")
    assert services.handle_webhook(first) == "ok"
    Webinar.objects.filter(pk=webinar.pk).update(live_started_at=None)
    assert services.handle_webhook(first) == "duplicate"
    assert Webinar.objects.get(pk=webinar.pk).live_started_at is None
    old = event("room_started", webinar, suffix="2", at=timezone.now() - timedelta(hours=2))
    assert services.handle_webhook(old) == "stale"
    assert (
        services.handle_webhook(event("room_started", webinar, suffix="3", room={"name": "olimp-x-zzz"}))
        == "ignored"
    )


# --- listy ---------------------------------------------------------------------------------------


def test_invitation_goes_once_to_audience_respecting_opt_out(
    webinars_on, fake_livekit, coordinator, participant, reviewer, django_capture_on_commit_callbacks
):
    other = ParticipantFactory(competition=webinars_on)
    grant_membership(other.user, webinars_on, CompetitionRole.PARTICIPANT)
    WebinarNotificationSettings.objects.create(user=other.user, email_on_webinar=False)
    webinar = make_webinar(webinars_on, audience=Audience.COMPETITION, description="Przynieś kartkę.")

    with django_capture_on_commit_callbacks(execute=True):
        assert services.announce(webinar, coordinator) is True
    assert services.announce(webinar, coordinator) is False

    assert sorted(address for message in mail.outbox for address in message.to) == [participant.user.email]
    message = mail.outbox[0]
    assert "Omówienie zadań" in message.subject
    assert "/webinars/" in message.body
    assert "Przynieś kartkę." in message.body
    assert "wss://" not in message.body and "token" not in message.body.lower()
    assert fake_livekit.calls == []


def test_recipients_follow_the_same_rule_as_the_panel(
    webinars_on, fake_livekit, participant, reviewer, stage, edition
):
    outsider = ParticipantFactory(competition=webinars_on)
    grant_membership(outsider.user, webinars_on, CompetitionRole.PARTICIPANT)
    StageEntryFactory(
        participant=outsider,
        stage=StageFactory(edition=edition, competition=webinars_on, kind=StageKind.DISTRICT),
        competition=webinars_on,
    )
    on_stage = make_webinar(webinars_on, audience=Audience.STAGE, stage=stage)
    committee = make_webinar(webinars_on, audience=Audience.COMMITTEE)

    assert list(services.audience_recipients(on_stage)) == [participant.user]
    assert list(services.audience_recipients(committee)) == [reviewer.user]
    for webinar in (on_stage, committee):
        for user in services.audience_recipients(webinar):
            assert viewer(user, webinars_on).in_audience(webinar)


def test_reminder_is_sent_once(webinars_on, fake_livekit, participant, django_capture_on_commit_callbacks):
    make_webinar(webinars_on, audience=Audience.COMPETITION, email_reminder=True, starts_in_minutes=30)
    make_webinar(webinars_on, audience=Audience.COMPETITION, email_reminder=False, starts_in_minutes=30)
    make_webinar(webinars_on, audience=Audience.COMPETITION, email_reminder=True, starts_in_minutes=600)

    with django_capture_on_commit_callbacks(execute=True):
        assert remind_webinars() == 1
        assert remind_webinars() == 0
    assert len(mail.outbox) == 1
    assert "Przypomnienie" in mail.outbox[0].subject


def test_no_mail_when_feature_is_off(
    competition, fake_livekit, participant, django_capture_on_commit_callbacks
):
    webinar = make_webinar(
        competition, audience=Audience.COMPETITION, email_reminder=True, starts_in_minutes=30
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert remind_webinars() == 0
        assert send_webinar_invitation(webinar.pk) == 0
    assert mail.outbox == []


def test_coordinators_are_valid_co_moderators(webinars_on, fake_livekit, coordinator):
    other = CoordinatorFactory()
    grant_membership(other, webinars_on, CompetitionRole.COORDINATOR)

    assert other in services.co_moderator_choices(webinars_on)
    webinar = services.create_webinar(
        webinars_on,
        coordinator,
        {"title": "x", "starts_at": timezone.now(), "duration_minutes": 60, "audience": Audience.COMPETITION},
        [other],
    )
    assert list(webinar.co_moderators.all()) == [other]


def test_processing_register_lists_webinars_only_with_flag(competition, fake_livekit):
    from apps.accounts.processing_register import WEBINARS_ACTIVITY, activities_for

    assert WEBINARS_ACTIVITY not in activities_for(competition)
    competition.feature_flags = {**(competition.feature_flags or {}), "webinars": True}
    assert WEBINARS_ACTIVITY in activities_for(competition)
