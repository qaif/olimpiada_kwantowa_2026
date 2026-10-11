"""Poprawki po przeglądzie STAGE-LK-01: zakładanie pokoi (H-1), nagranie tylko ze zgodą (H-2), zmiana
dostawcy przy nadzorze (M-2), ``livekit://`` bez serwera (M-3), decyzje moderatora przeżywające ponowne
wejście (L-4), osobne pokoje próby (L-5), limit poleceń (L-3), pseudonim w sesji (L-6)."""

from __future__ import annotations

import itertools
import json

import jwt
import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions import room_access
from apps.competitions.video import VideoProvider
from apps.proctoring import services
from apps.proctoring.models import InterviewRoomBlock, ProctoringRecording
from apps.tenancy.tests.factories import grant_membership
from apps.webinars.services import pseudonym
from apps.webinars.tests.fake_livekit import sign_webhook

from .conftest import ready_session
from .fake_livekit import API_SECRET
from .test_stage_rooms import JITSI_HOST, JITSI_SECRET, booked, interview_stage, proctored

pytestmark = pytest.mark.django_db
EVENTS = itertools.count(1)


@pytest.fixture
def both(settings, fake_livekit):
    """Jitsi z przepustkami i LiveKit naraz – jak w ``test_stage_rooms``."""
    settings.JITSI_JWT_APP_SECRET = JITSI_SECRET
    settings.JITSI_JWT_HOST = JITSI_HOST
    settings.JITSI_JWT_LEAD_MINUTES = 15
    settings.JITSI_JWT_GRACE_MINUTES = 60
    settings.JITSI_JWT_PRECHECK_MINUTES = 30
    return fake_livekit


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


def coordinator_of(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def token_claims(response):
    assert response.status_code == 200, response.content
    return jwt.decode(response.json()["token"], API_SECRET, algorithms=["HS256"])


# --- H-1: CreateRoom przed każdym tokenem ------------------------------------------------------------


def test_interview_and_precheck_rooms_are_created_before_tokens(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    room = room_access.livekit_room_of(booking.meeting_url)
    client = logged_in(client_for, competition, booking.entry.participant.user)
    interview = token_claims(client.post(f"/me/stages/{stage.pk}/interview/room/interview/token/"))
    precheck = token_claims(client.post(f"/me/stages/{stage.pk}/interview/room/precheck/token/"))
    assert interview["video"]["room"] == room and room in both.created
    assert precheck["video"]["room"] in both.created
    staff = logged_in(client_for, competition, coordinator_of(competition))
    staff_claims = token_claims(
        staff.post(f"/coordinator/interview-slots/{booking.slot.pk}/room/interview/token/")
    )
    assert staff_claims["video"]["room"] == room


def test_proctoring_rooms_are_created_before_tokens(
    proctoring_on, fake_livekit, config, stage, student, coordinator
):
    session = ready_session(stage, student, started=False)
    data = services.student_token(session, config, user=student.user)
    room = jwt.decode(data["token"], API_SECRET, algorithms=["HS256"])["video"]["room"]
    assert room == config.room_name("m") and room in fake_livekit.created
    scope = services.proctor_scope(coordinator, stage)
    services.proctor_token(scope, "m")
    assert fake_livekit.names().count("CreateRoom") == 2


def test_create_room_failure_is_502(both, competition, client_for):
    from apps.webinars.livekit import LiveKitUnavailable

    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    both.fail_with = LiveKitUnavailable("down")
    client = logged_in(client_for, competition, booking.entry.participant.user)
    response = client.post(f"/me/stages/{stage.pk}/interview/room/interview/token/")
    assert response.status_code == 502 and response.json()["code"] == "LIVEKIT_UNAVAILABLE"


# --- L-5: osobny pokój próby na zapis ---------------------------------------------------------------------


def test_precheck_room_is_separate_per_booking(both, competition, client_for):
    from apps.accounts.tests.factories import ParticipantFactory, UserFactory
    from apps.competitions.interviews import book_slot
    from apps.competitions.models import StageEntryStatus
    from apps.competitions.tests.factories import StageEntryFactory

    stage = interview_stage(competition, "livekit")
    first = booked(competition, stage)
    slot = first.slot
    slot.capacity = 2
    slot.save(update_fields=["capacity"])
    person = ParticipantFactory(
        competition=competition, birth_year=1990, user=UserFactory(first_name="Ewa", groups=["participant"])
    )
    grant_membership(person.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(
        participant=person, stage=stage, competition=competition, status=StageEntryStatus.QUALIFIED
    )
    second = book_slot(person, slot)
    assert second.meeting_url == first.meeting_url  # ten sam pokój rozmowy terminu
    rooms = set()
    for booking in (first, second):
        client = logged_in(client_for, competition, booking.entry.participant.user)
        claims = token_claims(client.post(f"/me/stages/{stage.pk}/interview/room/precheck/token/"))
        rooms.add(claims["video"]["room"])
    assert len(rooms) == 2 and all(room.endswith("-test") for room in rooms)


# --- M-3: livekit:// bez serwera ----------------------------------------------------------------------------


def test_livekit_room_without_server_answers_502_not_404(both, competition, client_for, settings):
    from apps.competitions.video import interview_access

    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    settings.LIVEKIT_URL = ""
    settings.JITSI_JWT_APP_SECRET = ""
    assert interview_access(booking)["mode"] == "platform"  # nigdy „link” z ``livekit://``
    client = logged_in(client_for, competition, booking.entry.participant.user)
    response = client.post(f"/me/stages/{stage.pk}/interview/room/interview/token/")
    assert response.status_code == 502
    assert "Serwer wideo nie odpowiada" in response.json()["detail"]


# --- L-4: decyzje moderatora przeżywają ponowne wejście ---------------------------------------------


def test_removed_participant_gets_no_new_token_until_readmitted(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    user = booking.entry.participant.user
    identity = pseudonym(user)
    moderator = logged_in(client_for, competition, coordinator_of(competition))
    control = f"/coordinator/interview-slots/{booking.slot.pk}/room-control/"
    assert moderator.post(control, {"action": "remove", "identity": identity}).status_code == 200
    block = InterviewRoomBlock.objects.get(slot=booking.slot, identity=identity)
    assert block.kind == "removed" and block.label.startswith("Jan")
    student = logged_in(client_for, competition, user)
    token_url = f"/me/stages/{stage.pk}/interview/room/interview/token/"
    refused = student.post(token_url)
    assert refused.status_code == 403 and refused.json()["code"] == "ROOM_REMOVED"
    # Strona pokoju moderatora pokazuje decyzję z przyciskiem „Wpuść ponownie”.
    page = moderator.get(f"/coordinator/interview-slots/{booking.slot.pk}/room/interview/").content.decode()
    assert "Wpuść ponownie" in page and identity in page
    assert moderator.post(control, {"action": "readmit", "identity": identity}).status_code == 200
    assert student.post(token_url).status_code == 200


def test_muted_participant_reconnects_without_publish_until_given_voice(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    user = booking.entry.participant.user
    identity = pseudonym(user)
    room = room_access.livekit_room_of(booking.meeting_url)
    both.rooms.setdefault(room, {})[identity] = []
    moderator = logged_in(client_for, competition, coordinator_of(competition))
    control = f"/coordinator/interview-slots/{booking.slot.pk}/room-control/"
    moderator.post(control, {"action": "listener", "identity": identity})
    student = logged_in(client_for, competition, user)
    token_url = f"/me/stages/{stage.pk}/interview/room/interview/token/"
    assert token_claims(student.post(token_url))["video"]["canPublish"] is False
    moderator.post(control, {"action": "speaker", "identity": identity})
    assert token_claims(student.post(token_url))["video"]["canPublish"] is True


def test_control_throttle_is_separate_and_json(both, competition, client_for, settings):
    from django.core.cache import cache

    rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    rates["interview_control"] = "1/hour"
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}
    cache.clear()
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    moderator = logged_in(client_for, competition, coordinator_of(competition))
    control = f"/coordinator/interview-slots/{booking.slot.pk}/room-control/"
    identity = pseudonym(booking.entry.participant.user)
    moderator.post(control, {"action": "readmit", "identity": identity})
    second = moderator.post(control, {"action": "readmit", "identity": identity})
    assert second.status_code == 429 and second.json()["code"] == "THROTTLED"
    # Wejścia (``video``) nie dzielą tego kubełka.
    assert (
        moderator.post(f"/coordinator/interview-slots/{booking.slot.pk}/room/interview/token/").status_code
        == 200
    )


# --- M-2: zmiana dostawcy przy nadzorze ----------------------------------------------------------------


def test_provider_change_away_from_livekit_refused_while_proctored(both, competition):
    from django import forms

    from apps.web.forms import StageForm, _clean_video_provider

    stage = interview_stage(competition, "livekit")
    proctored(competition, stage)
    form = StageForm(instance=stage)
    form.cleaned_data = {"video_provider": VideoProvider.JITSI}
    with pytest.raises(forms.ValidationError, match="nadzór zdalny"):
        _clean_video_provider(form)
    form.cleaned_data = {"video_provider": VideoProvider.LIVEKIT}
    assert _clean_video_provider(form) == VideoProvider.LIVEKIT


def test_config_ignored_once_stage_is_no_longer_proctorable(both, competition):
    stage = interview_stage(competition, "livekit")
    proctored(competition, stage)
    assert services.config_for(stage) is not None
    stage.video_provider = VideoProvider.JITSI
    stage.save(update_fields=["video_provider"])
    assert services.config_for(stage) is None


# --- H-2: nagranie rozmowy wyłącznie ze zgodą ---------------------------------------------------------


def _send(client, event):
    stamp = int(timezone.now().timestamp())
    body = json.dumps({"id": f"EVR_{next(EVENTS)}", "createdAt": stamp, **event}).encode()
    return client.post(
        "/integrations/livekit/webhook/",
        data=body,
        content_type="application/webhook+json",
        HTTP_AUTHORIZATION=sign_webhook(body),
    )


def _camera(client, room, identity, sid):
    return _send(
        client,
        {
            "event": "track_published",
            "room": {"name": room},
            "participant": {"identity": identity},
            "track": {"sid": sid, "source": "CAMERA"},
        },
    )


@pytest.fixture
def recorded_interview(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    config = proctored(competition, stage, record=True)
    participant = booking.entry.participant
    return {
        "stage": stage,
        "config": config,
        "participant": participant,
        "room": room_access.livekit_room_of(booking.meeting_url),
        "identity": pseudonym(participant.user),
        "client": client_for(competition),
    }


def test_no_consent_no_session_no_recording(recorded_interview, both, django_capture_on_commit_callbacks):
    world = recorded_interview
    with django_capture_on_commit_callbacks(execute=True):
        _camera(world["client"], world["room"], world["identity"], "TR_A")
    assert "StartTrackEgress" not in both.names()
    # Webhook nie zakłada sesji nadzoru (sesja bez zgody nie ma czego dokumentować).
    assert services.session_for(world["stage"], world["participant"], create=False) is None


def test_approved_alternative_is_not_recorded(recorded_interview, both, django_capture_on_commit_callbacks):
    world = recorded_interview
    session = services.session_for(world["stage"], world["participant"])
    services.give_consent(session, user=world["participant"].user, config=world["config"])
    services.decide_alternative(session, approve=True, decision="telefon", actor=None)
    with django_capture_on_commit_callbacks(execute=True):
        _camera(world["client"], world["room"], world["identity"], "TR_B")
    assert "StartTrackEgress" not in both.names()


def test_consent_withdrawn_after_token_stops_recording_start(recorded_interview, both):
    world = recorded_interview
    session = services.session_for(world["stage"], world["participant"])
    services.give_consent(session, user=world["participant"].user, config=world["config"])
    services.withdraw_consent(session, user=world["participant"].user)
    # Zadanie Celery z kolejki (np. zgoda wycofana między webhookiem a startem) – sprawdza jeszcze raz.
    assert services.start_recording(session.pk, "TR_C", world["room"]) is None
    assert not ProctoringRecording.objects.filter(session=session).exists()
    assert session.events.filter(kind="recording", detail__status="refused_no_consent").exists()


def test_recording_switched_on_mid_session_needs_new_consent(
    both, competition, client_for, django_capture_on_commit_callbacks
):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    config = proctored(competition, stage, record=False)
    participant = booking.entry.participant
    session = services.session_for(stage, participant)
    services.give_consent(session, user=participant.user, config=config)
    config.record = True
    config.save(update_fields=["record"])
    client = client_for(competition)
    with django_capture_on_commit_callbacks(execute=True):
        _camera(client, room_access.livekit_room_of(booking.meeting_url), pseudonym(participant.user), "TR_D")
    assert "StartTrackEgress" not in both.names()
    assert services.start_recording(session.pk, "TR_D") is None


# --- L-6: pseudonim konta zapisany w sesji ----------------------------------------------------------------


def test_session_stores_account_pseudonym(proctoring_on, config, stage, student):
    session = services.session_for(stage, student)
    assert session.account_identity == pseudonym(student.user)
