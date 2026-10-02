"""Wejście na rozmowę przez platformę (v0.39.0): uczestnik, koordynator, wyłączona funkcja.

Droga przez HTTP: kto dostaje przepustkę, do którego pokoju, z jakim oknem czasowym i z jakimi
nagłówkami odpowiedzi. Kształt samego tokenu ma własne testy
(``apps/competitions/tests/test_jitsi_jwt.py``); tutaj token jest dekodowany tylko po to, żeby
sprawdzić, że widok dał **ten** pokój, **to** okno i **tę** rolę.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.interviews import book_slot, cancel_booking
from apps.competitions.models import InterviewSlot, StageEntryStatus
from apps.competitions.tests.factories import CurrentEditionFactory, InterviewStageFactory, StageEntryFactory
from apps.competitions.video import VideoProvider
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

SECRET = "Q7xk2LmN9pRt4VwY8zA3bC6dE1fG5hJ0KsTuVxYz2a4b"
HOST = "meet.olimpiada.test"


@pytest.fixture
def jitsi(settings):
    settings.JITSI_JWT_APP_SECRET = SECRET
    settings.JITSI_JWT_HOST = HOST
    settings.JITSI_JWT_LEAD_MINUTES = 15
    settings.JITSI_JWT_GRACE_MINUTES = 60
    settings.JITSI_JWT_PRECHECK_MINUTES = 30
    return settings


@pytest.fixture
def stage():
    return InterviewStageFactory(
        edition=CurrentEditionFactory(year_label="XV (2026/2027)"),
        video_provider=VideoProvider.CUSTOM,
        video_base_url=f"https://{HOST}/",
    )


def qualified(stage, email: str, first: str = "Jan", last: str = "Kowalski"):
    person = ParticipantFactory(
        user=UserFactory(email=email, first_name=first, last_name=last, groups=["participant"])
    )
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    return person


@pytest.fixture
def participant(stage):
    return qualified(stage, "uczestnik@example.test")


def make_slot(stage, *, minutes_ahead: int = 10, capacity: int = 1, meeting_url: str = "") -> InterviewSlot:
    start = timezone.now() + timedelta(minutes=minutes_ahead)
    return InterviewSlot.objects.create(
        stage=stage,
        starts_at=start,
        ends_at=start + timedelta(minutes=20),
        capacity=capacity,
        meeting_url=meeting_url,
    )


def logged_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def token_from(location: str) -> dict:
    """Treść przepustki z adresu przekierowania (``…#jwt=%22<token>%22``)."""
    fragment = urlsplit(location).fragment
    assert fragment.startswith("jwt="), location
    token = json.loads(unquote(fragment[len("jwt=") :]))
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def join_url(stage) -> str:
    return f"/me/stages/{stage.pk}/interview/join/"


def precheck_url(stage) -> str:
    return f"/me/stages/{stage.pk}/interview/precheck/"


# --- uczestnik ----------------------------------------------------------------------------------


def test_participant_joins_own_room_with_a_participant_pass(jitsi, stage, participant):
    slot = make_slot(stage)
    booking = book_slot(participant, slot)

    response = logged_in(participant.user).get(join_url(stage))

    assert response.status_code == 302
    location = response["Location"]
    assert location.startswith(f"{booking.meeting_url}#jwt=")
    claims = token_from(location)
    room = booking.meeting_url.rsplit("/", 1)[1]
    assert claims["room"] == room
    assert claims["context"]["user"] == {"name": "Jan K."}
    assert claims["exp"] == int((slot.ends_at + timedelta(minutes=60)).timestamp())
    assert claims["nbf"] <= int((slot.starts_at - timedelta(minutes=15)).timestamp())
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"
    entry = AuditLog.objects.get(action="interview.joined")
    assert entry.diff == {"role": "participant", "kind": "interview"}


def test_precheck_pass_is_for_the_test_room_only(jitsi, stage, participant):
    booking = book_slot(participant, make_slot(stage, minutes_ahead=3 * 60))

    response = logged_in(participant.user).get(precheck_url(stage))

    assert response.status_code == 302
    claims = token_from(response["Location"])
    assert claims["room"] == booking.meeting_url.rsplit("/", 1)[1] + "-test"
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert "moderator" not in claims["context"]["user"]


def test_before_the_window_the_participant_gets_the_opening_time(jitsi, stage, participant):
    slot = make_slot(stage, minutes_ahead=2 * 60)
    book_slot(participant, slot)
    client = logged_in(participant.user)

    response = client.get(join_url(stage))

    assert response.status_code == 302
    assert response["Location"].startswith("/me/")
    content = client.get(response["Location"]).content.decode()
    opens = timezone.localtime(slot.starts_at - timedelta(minutes=15)).strftime("%H:%M")
    assert "otworzy się" in content
    assert opens in content
    assert not AuditLog.objects.filter(action="interview.joined").exists()


def test_after_the_window_there_is_no_pass(jitsi, stage, participant):
    slot = make_slot(stage, minutes_ahead=30)
    book_slot(participant, slot)
    InterviewSlot.objects.filter(pk=slot.pk).update(
        starts_at=timezone.now() - timedelta(hours=3), ends_at=timezone.now() - timedelta(hours=2, minutes=40)
    )

    response = logged_in(participant.user).get(join_url(stage))

    assert response.status_code == 302
    assert "#jwt=" not in response["Location"]


def test_someone_without_a_booking_gets_404(jitsi, stage, participant):
    book_slot(participant, make_slot(stage, capacity=2))
    other = qualified(stage, "inny@example.test")

    assert logged_in(other.user).get(join_url(stage)).status_code == 404
    assert logged_in(other.user).get(precheck_url(stage)).status_code == 404


def test_cancelled_booking_gets_404(jitsi, stage, participant):
    book_slot(participant, make_slot(stage, minutes_ahead=3 * 60))
    cancel_booking(participant, stage=stage)

    assert logged_in(participant.user).get(join_url(stage)).status_code == 404


def test_disqualified_participant_gets_404(jitsi, stage, participant):
    booking = book_slot(participant, make_slot(stage))
    booking.entry.status = StageEntryStatus.DISQUALIFIED
    booking.entry.save(update_fields=["status"])

    assert logged_in(participant.user).get(join_url(stage)).status_code == 404


def test_coordinator_cannot_use_the_participant_view(jitsi, stage, participant):
    book_slot(participant, make_slot(stage))

    assert logged_in(CoordinatorFactory()).get(join_url(stage)).status_code == 403


def test_the_pass_never_reaches_the_logs(jitsi, stage, participant, caplog):
    book_slot(participant, make_slot(stage))

    with caplog.at_level(logging.DEBUG):
        response = logged_in(participant.user).get(join_url(stage))

    token = json.loads(unquote(urlsplit(response["Location"]).fragment[len("jwt=") :]))
    assert token not in caplog.text
    assert all(token.split(".")[2] not in json.dumps(entry.diff) for entry in AuditLog.objects.all())


def test_panel_shows_join_buttons_and_hides_the_room_address(jitsi, stage, participant):
    booking = book_slot(participant, make_slot(stage))

    content = logged_in(participant.user).get("/me/").content.decode()

    assert join_url(stage) in content
    assert precheck_url(stage) in content
    assert "Dołącz do rozmowy" in content
    assert booking.meeting_url not in content


# --- funkcja wyłączona albo obcy serwer wideo -------------------------------------------------------


def test_without_a_secret_the_panel_shows_the_raw_link_and_the_views_are_404(settings, stage, participant):
    settings.JITSI_JWT_APP_SECRET = ""
    settings.JITSI_JWT_HOST = HOST
    booking = book_slot(participant, make_slot(stage))
    client = logged_in(participant.user)

    content = client.get("/me/").content.decode()

    assert booking.meeting_url in content
    assert f"{booking.meeting_url}-test" in content
    assert join_url(stage) not in content
    assert client.get(join_url(stage)).status_code == 404


def test_foreign_video_host_gets_no_pass(jitsi, stage, participant):
    stage.video_provider = VideoProvider.JITSI
    stage.video_base_url = "https://meet.jit.si/"
    stage.save(update_fields=["video_provider", "video_base_url"])
    booking = book_slot(participant, make_slot(stage))
    client = logged_in(participant.user)

    content = client.get("/me/").content.decode()

    assert booking.meeting_url in content
    assert join_url(stage) not in content
    assert client.get(join_url(stage)).status_code == 404


def test_manual_foreign_link_on_the_slot_is_shown_as_today(jitsi, stage, participant):
    booking = book_slot(participant, make_slot(stage, meeting_url="https://bbb.uczelnia.test/b/komisja-a"))

    content = logged_in(participant.user).get("/me/").content.decode()

    assert booking.meeting_url == "https://bbb.uczelnia.test/b/komisja-a"
    assert booking.meeting_url in content


# --- koordynator ----------------------------------------------------------------------------------


def test_coordinator_joins_the_slot_room_as_moderator(jitsi, stage, participant):
    slot = make_slot(stage)
    booking = book_slot(participant, slot)
    coordinator = CoordinatorFactory(first_name="Anna", last_name="Nowak")

    response = logged_in(coordinator).get(f"/coordinator/interview-slots/{slot.pk}/join/")

    assert response.status_code == 302
    claims = token_from(response["Location"])
    assert claims["room"] == booking.meeting_url.rsplit("/", 1)[1]
    assert claims["context"]["user"] == {"name": "Anna N.", "moderator": True}
    assert AuditLog.objects.get(action="interview.joined").diff == {
        "role": "coordinator",
        "kind": "interview",
    }


def test_coordinator_screen_links_the_join_view_not_the_room(jitsi, stage, participant):
    slot = make_slot(stage)
    booking = book_slot(participant, slot)

    content = (
        logged_in(CoordinatorFactory()).get(f"/coordinator/stages/{stage.pk}/interviews/").content.decode()
    )

    assert f"/coordinator/interview-slots/{slot.pk}/join/" in content
    assert f'href="{booking.meeting_url}"' not in content


def test_participant_cannot_use_the_coordinator_join(jitsi, stage, participant):
    slot = make_slot(stage)
    book_slot(participant, slot)

    assert logged_in(participant.user).get(f"/coordinator/interview-slots/{slot.pk}/join/").status_code == 403


def test_coordinator_of_another_competition_gets_404(
    jitsi, stage, participant, client_for, other_competition
):
    from apps.accounts.models import CompetitionRole
    from apps.tenancy.tests.factories import grant_membership

    slot = make_slot(stage)
    book_slot(participant, slot)
    stranger = CoordinatorFactory()
    grant_membership(stranger, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(stranger)

    assert client.get(f"/coordinator/interview-slots/{slot.pk}/join/").status_code == 404
