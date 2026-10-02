"""Komisja prowadzi rozmowy kwalifikacyjne (v0.39.0): karta w panelu komisji i wejście jako gospodarz.

Pilnowane:

- **kto** – aktywny recenzent albo członek komisji odwoławczej **tego** konkursu; członek
  oczekujący, zawieszony, z innego konkursu i uczestnik – nie,
- **kiedy** – to samo okno, co koordynatora (15 min przed – 60 min po terminie); poza nim
  komunikat, bez przepustki,
- **co widać** – imię i inicjał nazwiska, nic więcej (komisja ocenia gdzie indziej anonimowo),
- **wyłączona funkcja** – ani karty, ani wejścia, ani surowego adresu pokoju.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import GROUP_APPEALS, CommitteeStatus
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    ParticipantFactory,
    PendingReviewerFactory,
    UserFactory,
)
from apps.competitions.interviews import book_slot
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
    return settings


@pytest.fixture
def stage():
    return InterviewStageFactory(
        edition=CurrentEditionFactory(year_label="XV (2026/2027)"),
        video_provider=VideoProvider.CUSTOM,
        video_base_url=f"https://{HOST}/",
    )


@pytest.fixture
def booking(stage):
    """Zapis Jana Kowalskiego (szkoła i kod w profilu – nie mogą wyjść poza ekran koordynatora)."""
    person = ParticipantFactory(
        user=UserFactory(
            email="jan.kowalski@example.test", first_name="Jan", last_name="Kowalski", groups=["participant"]
        )
    )
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    start = timezone.now() + timedelta(minutes=10)
    slot = InterviewSlot.objects.create(stage=stage, starts_at=start, ends_at=start + timedelta(minutes=20))
    return book_slot(person, slot)


def logged_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def claims_from(location: str) -> dict:
    fragment = urlsplit(location).fragment
    token = json.loads(unquote(fragment[len("jwt=") :]))
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def join(slot) -> str:
    return f"/review/interview-slots/{slot.pk}/join/"


def appeals_member(**kwargs):
    return CommitteeMemberFactory(
        user=UserFactory(groups=[GROUP_APPEALS]),
        status=CommitteeStatus.ACTIVE,
        is_appeals_committee=True,
        **kwargs,
    )


# --- karta w panelu -----------------------------------------------------------------------------


def test_reviewer_sees_the_slot_with_short_names_only(jitsi, booking):
    content = logged_in(ActiveReviewerFactory().user).get("/review/").content.decode()

    assert "Rozmowy kwalifikacyjne" in content
    assert "Jan K." in content
    assert "Kowalski" not in content
    assert "jan.kowalski@example.test" not in content
    assert booking.entry.participant.public_code not in content
    assert join(booking.slot) in content
    assert booking.meeting_url not in content


def test_appeals_member_sees_the_slot(jitsi, booking):
    content = logged_in(appeals_member().user).get("/appeals/").content.decode()

    assert "Jan K." in content
    assert join(booking.slot) in content


def test_past_slots_and_slots_without_bookings_are_not_listed(jitsi, stage, booking):
    start = timezone.now() + timedelta(hours=2)
    empty = InterviewSlot.objects.create(stage=stage, starts_at=start, ends_at=start + timedelta(minutes=20))
    InterviewSlot.objects.filter(pk=booking.slot_id).update(
        starts_at=timezone.now() - timedelta(hours=3), ends_at=timezone.now() - timedelta(hours=2, minutes=40)
    )

    content = logged_in(ActiveReviewerFactory().user).get("/review/").content.decode()

    assert join(booking.slot) not in content
    assert join(empty) not in content


def test_without_a_secret_there_is_no_card_no_join_and_no_raw_link(settings, booking):
    settings.JITSI_JWT_APP_SECRET = ""
    settings.JITSI_JWT_HOST = HOST
    client = logged_in(ActiveReviewerFactory().user)

    content = client.get("/review/").content.decode()

    assert "Rozmowy kwalifikacyjne" not in content
    assert booking.meeting_url not in content
    assert client.get(join(booking.slot)).status_code == 404


def test_foreign_room_is_listed_without_any_link(jitsi, stage):
    person = ParticipantFactory(user=UserFactory(first_name="Ola", last_name="Nowak", groups=["participant"]))
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    start = timezone.now() + timedelta(minutes=10)
    slot = InterviewSlot.objects.create(
        stage=stage,
        starts_at=start,
        ends_at=start + timedelta(minutes=20),
        meeting_url="https://bbb.uczelnia.test/b/komisja-a",
    )
    book_slot(person, slot)
    client = logged_in(ActiveReviewerFactory().user)

    content = client.get("/review/").content.decode()

    assert "Ola N." in content
    assert "bbb.uczelnia.test" not in content
    assert client.get(join(slot)).status_code == 404


# --- wejście ----------------------------------------------------------------------------------------


def test_reviewer_joins_as_moderator_within_the_window(jitsi, booking):
    member = ActiveReviewerFactory()
    member.user.first_name, member.user.last_name = "Ewa", "Recenzentka"
    member.user.save(update_fields=["first_name", "last_name"])

    response = logged_in(member.user).get(join(booking.slot))

    assert response.status_code == 302
    assert "no-store" in response["Cache-Control"]
    claims = claims_from(response["Location"])
    assert claims["room"] == booking.meeting_url.rsplit("/", 1)[1]
    assert claims["context"]["user"] == {"name": "Ewa R.", "moderator": True}
    assert claims["exp"] == int((booking.slot.ends_at + timedelta(minutes=60)).timestamp())
    assert AuditLog.objects.get(action="interview.joined").diff == {"role": "committee", "kind": "interview"}


def test_appeals_member_joins_as_moderator(jitsi, booking):
    response = logged_in(appeals_member().user).get(join(booking.slot))

    assert claims_from(response["Location"])["context"]["user"]["moderator"] is True


def test_precheck_for_the_committee_has_no_moderator(jitsi, booking):
    response = logged_in(ActiveReviewerFactory().user).get(
        f"/review/interview-slots/{booking.slot_id}/precheck/"
    )

    claims = claims_from(response["Location"])
    assert claims["room"].endswith("-test")
    assert "moderator" not in claims["context"]["user"]


def test_outside_the_window_there_is_a_message_and_no_token(jitsi, booking):
    later = timezone.now() + timedelta(hours=3)
    InterviewSlot.objects.filter(pk=booking.slot_id).update(
        starts_at=later, ends_at=later + timedelta(minutes=20)
    )
    client = logged_in(ActiveReviewerFactory().user)

    response = client.get(join(booking.slot))

    assert response.status_code == 302
    assert "#jwt=" not in response["Location"]
    assert "otworzy się" in client.get(response["Location"]).content.decode()
    assert not AuditLog.objects.filter(action="interview.joined").exists()


@pytest.mark.parametrize("status", [CommitteeStatus.PENDING, CommitteeStatus.SUSPENDED])
def test_inactive_member_is_refused(jitsi, booking, status):
    member = PendingReviewerFactory(status=status)

    assert logged_in(member.user).get(join(booking.slot)).status_code == 403


def test_participant_is_refused(jitsi, booking):
    assert logged_in(booking.entry.participant.user).get(join(booking.slot)).status_code == 403


def test_member_of_another_competition_gets_404(jitsi, booking, client_for, other_competition):
    stranger = ActiveReviewerFactory(competition=other_competition)
    client = client_for(other_competition)
    client.force_login(stranger.user)

    assert client.get(join(booking.slot)).status_code == 404
    assert "Jan K." not in client.get("/review/").content.decode()


def test_member_of_another_competition_is_refused_here(jitsi, booking, other_competition):
    """Pod domeną tego konkursu profil z innego konkursu nie jest członkiem komisji – 403."""
    stranger = ActiveReviewerFactory(competition=other_competition)

    assert logged_in(stranger.user).get(join(booking.slot)).status_code == 403
