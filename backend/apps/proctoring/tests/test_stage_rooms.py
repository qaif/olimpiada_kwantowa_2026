"""Pokoje rozmów etapu w LiveKit (STAGE-LK-01): wybór dostawcy, **ta sama macierz ról** dla Jitsi
i LiveKit, polecenia moderatora, połączenie z nadzorem, flaga wyłączona, izolacja konkursów.

Test parytetu buduje dla każdego scenariusza **dwa** światy – etap z pokojem na naszym Jitsi i etap
z pokojem LiveKit – i przechodzi tę samą drogę wejścia (``interview-join``, ``…-slot-join``, próby
sprzętu). Wynik sprowadzamy do wspólnej postaci: 404/403, logowanie, powrót z komunikatem albo bilet
(moderator tak/nie, koniec ważności, pokój próby) – i porównujemy go między dostawcami.
"""

from __future__ import annotations

import base64
import itertools
import json
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import jwt
import pytest
from django.utils import timezone

from apps.accounts.models import CommitteeStatus, CompetitionRole
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions import room_access
from apps.competitions.interviews import book_slot
from apps.competitions.models import InterviewSlot, StageEntryStatus, StageKind
from apps.competitions.tests.factories import InterviewStageFactory, StageEntryFactory
from apps.competitions.video import VideoProvider, build_meeting_url
from apps.core.models import AuditLog
from apps.proctoring import services
from apps.proctoring.models import ProctoringConfig, ProctoringRecording
from apps.tenancy.tests.factories import grant_membership
from apps.webinars.tests.fake_livekit import sign_webhook

from .fake_livekit import API_SECRET

pytestmark = pytest.mark.django_db

JITSI_SECRET = "Q7xk2LmN9pRt4VwY8zA3bC6dE1fG5hJ0KsTuVxYz2a4b"
JITSI_HOST = "meet.olimpiada.test"
PROVIDERS = ("jitsi", "livekit")
COUNTER = itertools.count(1)


@pytest.fixture
def both(settings, fake_livekit):
    settings.JITSI_JWT_APP_SECRET = JITSI_SECRET
    settings.JITSI_JWT_HOST = JITSI_HOST
    settings.JITSI_JWT_LEAD_MINUTES = 15
    settings.JITSI_JWT_GRACE_MINUTES = 60
    settings.JITSI_JWT_PRECHECK_MINUTES = 30
    return fake_livekit


def interview_stage(competition, provider: str):
    from apps.competitions.services import current_edition
    from apps.competitions.tests.factories import CurrentEditionFactory

    edition = current_edition(competition) or CurrentEditionFactory(competition=competition)
    # Rodzaj „Runda” – jedyny bez więzu „jeden na edycję”, a test zakłada po etapie na dostawcę.
    stage = InterviewStageFactory(
        edition=edition, competition=competition, kind=StageKind.ROUND, name=f"Rozmowy {next(COUNTER)}"
    )
    if provider == "jitsi":
        stage.video_provider, stage.video_base_url = VideoProvider.CUSTOM, f"https://{JITSI_HOST}/"
    else:
        stage.video_provider = VideoProvider.LIVEKIT
    stage.save(update_fields=["video_provider", "video_base_url"])
    return stage


def booked(competition, stage, *, minutes_ahead: int = 10, status=StageEntryStatus.QUALIFIED):
    person = ParticipantFactory(
        competition=competition,
        birth_year=1990,  # pełnoletni – zgoda opiekuna nie wchodzi w drogę parytetowi ról
        user=UserFactory(first_name="Jan", last_name="Kowalski", groups=["participant"]),
    )
    grant_membership(person.user, competition, CompetitionRole.PARTICIPANT)
    StageEntryFactory(participant=person, stage=stage, competition=competition, status=QUALIFIED)
    start = timezone.now() + timedelta(minutes=minutes_ahead)
    slot = InterviewSlot.objects.create(stage=stage, starts_at=start, ends_at=start + timedelta(minutes=20))
    booking = book_slot(person, slot)
    if status != QUALIFIED:
        booking.entry.status = status
        booking.entry.save(update_fields=["status"])
    return booking


QUALIFIED = StageEntryStatus.QUALIFIED


def outcome(client, path: str, provider: str):
    """Wynik wejścia sprowadzony do postaci wspólnej dla dostawców."""
    response = client.get(path)
    if response.status_code in (403, 404):
        return response.status_code
    assert response.status_code == 302, response.status_code
    location = response["Location"]
    if "login" in location:
        return "login"
    if provider == "jitsi":
        if "#jwt=" not in location:
            return "back"
        token = json.loads(unquote(urlsplit(location).fragment[len("jwt=") :]))
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        moderator = bool(claims["context"]["user"].get("moderator"))
        return (
            "pass",
            moderator,
            claims["exp"],
            claims["room"].endswith("-test"),
            claims["context"]["user"].get("name"),
        )
    if "/room/" not in location:
        return "back"
    token_response = client.post(f"{location}token/")
    assert token_response.status_code == 200, token_response.content
    data = token_response.json()
    claims = jwt.decode(data["token"], API_SECRET, algorithms=["HS256"])
    video = claims["video"]
    assert video["canPublish"] and video["canSubscribe"] and video["canPublishData"]
    # Przegląd L-1: przeglądarka nie dostaje ``roomAdmin`` – moderatora poznaje interfejs po roli
    # z odpowiedzi, a polecenia idą przez platformę.
    assert "roomAdmin" not in video
    return (
        "pass",
        data["role"] == "presenter",
        claims["exp"],
        video["room"].endswith("-test"),
        claims["name"],
    )


def world(competition, provider, scenario, other_competition=None):
    """Konto i adres wejścia dla scenariusza w etapie danego dostawcy."""
    stage = interview_stage(competition, provider)
    booking = booked(
        competition,
        stage,
        minutes_ahead=120 if scenario.endswith("early") else 10,
        status=StageEntryStatus.DISQUALIFIED if scenario == "participant_disqualified" else QUALIFIED,
    )
    slot = booking.slot
    join, precheck = f"/me/stages/{stage.pk}/interview/join/", f"/me/stages/{stage.pk}/interview/precheck/"
    coord_join = f"/coordinator/interview-slots/{slot.pk}/join/"
    coord_precheck = f"/coordinator/interview-slots/{slot.pk}/precheck/"
    committee_join = f"/review/interview-slots/{slot.pk}/join/"
    if scenario in ("participant", "participant_early", "participant_disqualified"):
        return booking.entry.participant.user, join
    if scenario == "participant_precheck":
        return booking.entry.participant.user, precheck
    if scenario == "participant_without_booking":
        stranger = ParticipantFactory(competition=competition, user=UserFactory(groups=["participant"]))
        grant_membership(stranger.user, competition, CompetitionRole.PARTICIPANT)
        return stranger.user, join
    if scenario == "participant_on_coordinator_url":
        return booking.entry.participant.user, coord_join
    coordinator = CoordinatorFactory(first_name="Ola", last_name="Koordynatorka")
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    if scenario in ("coordinator", "coordinator_early"):
        return coordinator, coord_join
    if scenario == "coordinator_precheck":
        return coordinator, coord_precheck
    member = ActiveReviewerFactory(competition=competition)
    member.user.first_name, member.user.last_name = "Ewa", "Recenzentka"
    member.user.save(update_fields=["first_name", "last_name"])
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    if scenario == "committee":
        return member.user, committee_join
    if scenario == "committee_suspended":
        member.status = CommitteeStatus.SUSPENDED
        member.save(update_fields=["status"])
        return member.user, committee_join
    if scenario == "anonymous":
        return None, join
    raise AssertionError(scenario)


#: Macierz ról: scenariusz → oczekiwany wynik (``pass`` – z flagą moderatora i pokojem próby).
MATRIX = {
    "participant": ("pass", False, False),
    "participant_precheck": ("pass", False, True),
    "participant_early": "back",
    "participant_disqualified": 404,
    "participant_without_booking": 404,
    "participant_on_coordinator_url": 403,
    "coordinator": ("pass", True, False),
    "coordinator_precheck": ("pass", False, True),
    "coordinator_early": "back",
    "committee": ("pass", True, False),
    "committee_suspended": 403,
    "anonymous": "login",
}


@pytest.mark.parametrize("scenario", sorted(MATRIX))
def test_same_role_matrix_for_jitsi_and_livekit(both, competition, client_for, scenario):
    results = {}
    for provider in PROVIDERS:
        user, path = world(competition, provider, scenario)
        client = client_for(competition)
        if user is not None:
            client.force_login(user)
        results[provider] = outcome(client, path, provider)
    expected = MATRIX[scenario]
    for provider, result in results.items():
        if isinstance(expected, tuple):
            assert isinstance(result, tuple), (provider, result)
            assert (result[0], result[1], result[3]) == expected, (provider, result)
        else:
            assert result == expected, (provider, result)
    # Ten sam koniec ważności biletu (okno terminu) i ta sama nazwa w pokoju u obu dostawców –
    # terminy w obu światach są założone w tej samej chwili z dokładnością do sekund.
    if isinstance(expected, tuple):
        assert abs(results["jitsi"][2] - results["livekit"][2]) <= 5
        assert results["jitsi"][4] == results["livekit"][4]


def test_other_competition_coordinator_gets_404_for_both(both, competition, other_competition, client_for):
    for provider in PROVIDERS:
        stage = interview_stage(competition, provider)
        slot = booked(competition, stage).slot
        stranger = CoordinatorFactory()
        grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
        client = client_for(other_competition)
        client.force_login(stranger)
        assert client.get(f"/coordinator/interview-slots/{slot.pk}/join/").status_code == 404
        if provider == "livekit":
            assert (
                client.post(f"/coordinator/interview-slots/{slot.pk}/room/interview/token/").status_code
                == 404
            )


# --- wybór dostawcy ---------------------------------------------------------------------------------


def test_provider_selection_builds_the_right_room(both, competition):
    livekit_stage = interview_stage(competition, "livekit")
    jitsi_stage = interview_stage(competition, "jitsi")
    livekit_url = build_meeting_url(livekit_stage, booked(competition, livekit_stage).slot)
    jitsi_url = build_meeting_url(jitsi_stage, booked(competition, jitsi_stage).slot)
    assert livekit_url.startswith("livekit://olimpiada-")
    assert jitsi_url.startswith(f"https://{JITSI_HOST}/olimpiada-")
    assert room_access.provider_of(livekit_url) == "livekit"
    assert room_access.provider_of(jitsi_url) == "jitsi"


def test_livekit_choice_hidden_and_refused_without_server(settings, competition):
    from apps.web.forms import StageForm

    settings.LIVEKIT_URL = ""
    form = StageForm()
    assert VideoProvider.LIVEKIT not in [value for value, _label in form.fields["video_provider"].choices]


def test_livekit_choice_available_with_server(both, competition):
    from apps.web.forms import StageForm

    form = StageForm()
    assert VideoProvider.LIVEKIT in [value for value, _label in form.fields["video_provider"].choices]


def test_livekit_room_without_server_is_still_a_platform_room(settings):
    """Przegląd M-3: ``livekit://`` to identyfikator, nie link – nigdy nie trafia na ekran jako odnośnik."""
    settings.LIVEKIT_URL = ""
    assert room_access.is_platform_room("livekit://olimpiada-x-abc")
    assert room_access.provider_of("livekit://olimpiada-x-abc") == "livekit"


def test_dashboard_and_letters_treat_livekit_room_as_platform_room(both, competition, client_for):
    from apps.competitions.video import interview_access, letter_link_lines

    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    assert interview_access(booking)["mode"] == "platform"
    lines = letter_link_lines(stage, booking.meeting_url, competition=competition)
    assert f"/me/stages/{stage.pk}/interview/join/" in lines[0]
    assert "livekit://" not in " ".join(lines)


# --- strona pokoju i polecenia moderatora ---------------------------------------------------------------


def test_livekit_room_page_has_no_token_and_token_is_audited(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    client = client_for(competition)
    client.force_login(booking.entry.participant.user)
    page = client.get(f"/me/stages/{stage.pk}/interview/room/interview/")
    assert page.status_code == 200
    assert "token" not in page.content.decode().lower().split("data-token-url")[0][-200:]
    assert not AuditLog.objects.filter(action="interview.joined").exists()
    response = client.post(f"/me/stages/{stage.pk}/interview/room/interview/token/")
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    assert AuditLog.objects.filter(action="interview.joined", diff__role="participant").count() == 1


def test_jitsi_room_has_no_livekit_page(both, competition, client_for):
    stage = interview_stage(competition, "jitsi")
    booking = booked(competition, stage)
    client = client_for(competition)
    client.force_login(booking.entry.participant.user)
    assert client.get(f"/me/stages/{stage.pk}/interview/room/interview/").status_code == 404


def test_moderator_can_remove_and_mute_participant_can_not(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    slot = booking.slot
    room = room_access.livekit_room_of(booking.meeting_url)
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    from apps.webinars.services import pseudonym

    identity = pseudonym(booking.entry.participant.user)
    both.rooms.setdefault(room, {})[identity] = {"can_publish": True}
    client = client_for(competition)
    client.force_login(coordinator)
    url = f"/coordinator/interview-slots/{slot.pk}/room-control/"
    assert client.post(url, {"action": "listener", "identity": identity}).status_code == 200
    assert both.payload("UpdateParticipant")["permission"]["can_publish"] is False
    assert client.post(url, {"action": "remove", "identity": identity}).status_code == 200
    assert both.payload("RemoveParticipant") == {"room": room, "identity": identity}
    assert AuditLog.objects.filter(action="interview.room_control").count() == 2
    # Przegląd L-2: audyt wskazuje, kogo dotyczyła decyzja – pseudonimem z pokoju.
    assert set(
        AuditLog.objects.filter(action="interview.room_control").values_list("diff__identity", flat=True)
    ) == {identity}
    student = client_for(competition)
    student.force_login(booking.entry.participant.user)
    assert student.post(url, {"action": "remove", "identity": identity}).status_code == 403


def test_control_outside_the_window_is_refused(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    slot = booked(competition, stage, minutes_ahead=180).slot
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(coordinator)
    response = client.post(f"/coordinator/interview-slots/{slot.pk}/room-control/", {"action": "remove"})
    assert response.status_code == 409 and response.json()["code"] == "ROOM_NOT_YET"


# --- LiveKit + nadzór -------------------------------------------------------------------------------------


def proctored(competition, stage, *, record=False):
    competition.feature_flags = {**(competition.feature_flags or {}), "proctoring": True}
    competition.save(update_fields=["feature_flags"])
    return ProctoringConfig.objects.create(stage=stage, enabled=True, record=record)


def test_proctored_interview_requires_consent_and_check(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    config = proctored(competition, stage)
    participant = booking.entry.participant
    client = client_for(competition)
    client.force_login(participant.user)
    token_url = f"/me/stages/{stage.pk}/interview/room/interview/token/"
    refused = client.post(token_url)
    assert refused.status_code == 409 and refused.json()["code"] == "PROCTORING_REQUIRED"
    # Próba sprzętu nie wymaga nadzoru – jak w Jitsi.
    assert client.post(f"/me/stages/{stage.pk}/interview/room/precheck/token/").status_code == 200
    session = services.session_for(stage, participant)
    services.give_consent(session, user=participant.user, config=config)
    session.check_passed_at = timezone.now()
    session.save(update_fields=["check_passed_at"])
    assert client.post(token_url).status_code == 200
    assert services.step(session, config) == "interview"


def test_interview_stage_is_proctorable_only_on_livekit(both, competition):
    assert services.proctorable(interview_stage(competition, "livekit"))
    assert not services.proctorable(interview_stage(competition, "jitsi"))


def test_flag_off_proctoring_config_is_ignored(both, competition, client_for):
    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    ProctoringConfig.objects.create(stage=stage, enabled=True)  # konfiguracja bez flagi konkursu
    client = client_for(competition)
    client.force_login(booking.entry.participant.user)
    assert client.post(f"/me/stages/{stage.pk}/interview/room/interview/token/").status_code == 200


def test_interview_room_webhooks_feed_the_proctoring_session_and_recording(
    both, competition, client_for, django_capture_on_commit_callbacks
):
    from apps.webinars.services import pseudonym

    stage = interview_stage(competition, "livekit")
    booking = booked(competition, stage)
    config = proctored(competition, stage, record=True)
    room = room_access.livekit_room_of(booking.meeting_url)
    identity = pseudonym(booking.entry.participant.user)
    # Zgoda na nadzór **z nagrywaniem** – bez niej kamera nie jest nagrywana (przegląd H-2).
    participant = booking.entry.participant
    session = services.session_for(stage, participant)
    services.give_consent(session, user=participant.user, config=config)
    client = client_for(competition)

    def send(event):
        stamp = int(timezone.now().timestamp())
        body = json.dumps({"id": f"EVS_{next(COUNTER)}", "createdAt": stamp, **event}).encode()
        return client.post(
            "/integrations/livekit/webhook/",
            data=body,
            content_type="application/webhook+json",
            HTTP_AUTHORIZATION=sign_webhook(body),
        )

    who = {"identity": identity}
    assert (
        send({"event": "participant_joined", "room": {"name": room}, "participant": who}).status_code == 200
    )
    with django_capture_on_commit_callbacks(execute=True):
        send(
            {
                "event": "track_published",
                "room": {"name": room},
                "participant": who,
                "track": {"sid": "TR_CAM", "source": "CAMERA"},
            }
        )
    session = services.session_for(stage, booking.entry.participant)
    assert session.connected
    assert {"connected", "camera_on"} <= set(session.events.values_list("kind", flat=True))
    egress = both.payload("StartTrackEgress")
    assert egress["room_name"] == room and egress["track_id"] == "TR_CAM"
    assert ProctoringRecording.objects.filter(session=session).exists()
