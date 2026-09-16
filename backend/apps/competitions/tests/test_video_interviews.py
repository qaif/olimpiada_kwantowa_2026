"""Pokój wideo rozmowy kwalifikacyjnej: nazwa, pierwszeństwa, list i przypomnienie.

Trzy rzeczy, których nie da się rozdzielić:

- **prywatność pokoju**. Publiczna instancja Jitsi nie wymaga logowania, więc nazwa pokoju *jest*
  poświadczeniem. Test pilnuje, że nazwa jest losowa (dwa wywołania nie dają tej samej) i że adres
  nie wycieka na wspólną listę terminów, tylko stoi przy własnym zapisie uczestnika,
- **pierwszeństwa**. Ręczny link koordynatora wygrywa ze wszystkim, a dwie osoby zapisane na ten
  sam termin mają zastać się nawzajem – czyli dostać ten sam pokój,
- **przypomnienie**. Ma pójść raz, dobę przed rozmową, i zostawić po sobie znacznik.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.interviews import book_slot
from apps.competitions.models import InterviewBooking, InterviewSlot, StageEntryStatus
from apps.competitions.tasks import remind_interviews
from apps.competitions.video import (
    DEFAULT_VIDEO_BASE_URL,
    PRECHECK_SUFFIX,
    VideoProvider,
    build_meeting_url,
    precheck_url,
    slugify_ascii,
)

from .factories import CurrentEditionFactory, InterviewStageFactory, StageEntryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def stage():
    """Etap rozmowy z włączonym dostawcą wideo – domyślnie publiczna instancja Jitsi."""
    return InterviewStageFactory(
        edition=CurrentEditionFactory(year_label="XV (2026/2027)"),
        video_provider=VideoProvider.JITSI,
        video_base_url=DEFAULT_VIDEO_BASE_URL,
    )


@pytest.fixture
def participant(stage):
    """Uczestnik zakwalifikowany do etapu – tylko taki może się zapisać na rozmowę."""
    person = ParticipantFactory(user=UserFactory(email="uczestnik@example.test", groups=["participant"]))
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    return person


def make_slot(stage, *, minutes_ahead: int = 60, capacity: int = 1, meeting_url: str = "") -> InterviewSlot:
    start = timezone.now() + timedelta(minutes=minutes_ahead)
    return InterviewSlot.objects.create(
        stage=stage,
        starts_at=start,
        ends_at=start + timedelta(minutes=20),
        capacity=capacity,
        meeting_url=meeting_url,
    )


def qualified(stage, email: str):
    person = ParticipantFactory(user=UserFactory(email=email, groups=["participant"]))
    StageEntryFactory(participant=person, stage=stage, status=StageEntryStatus.QUALIFIED)
    return person


# --- (a) nazwa pokoju ----------------------------------------------------------------------------


def test_room_name_carries_edition_and_is_random(stage):
    slot = make_slot(stage)

    first = build_meeting_url(stage, slot)
    second = build_meeting_url(stage, slot)

    assert first.startswith("https://meet.jit.si/olimpiada-")
    assert slugify_ascii(stage.edition.year_label) in first
    # Losowa końcówka jest jedyną częścią, która czyni pokój prywatnym – dwa wywołania nie mogą
    # dać tej samej nazwy, bo wtedy nazwa byłaby przewidywalna.
    assert first != second


def test_stage_without_a_provider_gets_no_link():
    plain = InterviewStageFactory(edition=CurrentEditionFactory())
    slot = make_slot(plain)

    assert build_meeting_url(plain, slot) == ""


def test_custom_provider_uses_its_own_server(stage):
    stage.video_provider = VideoProvider.CUSTOM
    stage.video_base_url = "https://spotkania.uczelnia.test/"
    stage.save(update_fields=["video_provider", "video_base_url"])
    slot = make_slot(stage)

    assert build_meeting_url(stage, slot).startswith("https://spotkania.uczelnia.test/olimpiada-")


def test_precheck_url_is_a_separate_empty_room():
    room = "https://meet.jit.si/olimpiada-x"

    assert precheck_url(room) == room + PRECHECK_SUFFIX
    assert precheck_url("") == ""


# --- (b) zapis: pierwszeństwa i list --------------------------------------------------------------


def test_booking_gets_a_link_and_it_goes_in_the_letter(
    stage, participant, django_capture_on_commit_callbacks
):
    slot = make_slot(stage)

    with django_capture_on_commit_callbacks(execute=True):
        booking = book_slot(participant, slot)

    assert booking.meeting_url.startswith("https://meet.jit.si/olimpiada-")
    message = mail.outbox[-1]
    assert booking.meeting_url in message.body
    assert precheck_url(booking.meeting_url) in message.body
    assert "kamer" in message.body


def test_manual_link_on_the_slot_wins(stage, participant):
    slot = make_slot(stage, meeting_url="https://wlasny.pokoj.test/komisja-a")

    booking = book_slot(participant, slot)

    assert booking.meeting_url == "https://wlasny.pokoj.test/komisja-a"


def test_two_participants_on_one_slot_share_the_room(stage, participant):
    slot = make_slot(stage, capacity=2)
    other = qualified(stage, "druga@example.test")

    first = book_slot(participant, slot)
    second = book_slot(other, slot)

    assert first.meeting_url == second.meeting_url


def test_moving_to_another_slot_gives_another_room(stage, participant):
    first_slot = make_slot(stage, minutes_ahead=60)
    second_slot = make_slot(stage, minutes_ahead=120)

    first_url = book_slot(participant, first_slot).meeting_url
    moved = book_slot(participant, second_slot)

    assert moved.meeting_url and moved.meeting_url != first_url


def test_the_link_of_another_participant_never_reaches_the_panel(stage, participant):
    """Adres pokoju stoi wyłącznie przy **własnym** terminie – lista terminów go nie niesie."""
    slot = make_slot(stage, capacity=2)
    other = qualified(stage, "druga@example.test")
    other_booking = book_slot(other, slot)

    client = Client()
    client.force_login(participant.user)
    content = client.get("/me/").content.decode()

    assert other_booking.meeting_url not in content


def test_participant_panel_shows_the_link_and_the_hardware_check(stage, participant):
    booking = book_slot(participant, make_slot(stage))

    client = Client()
    client.force_login(participant.user)
    content = client.get("/me/").content.decode()

    assert booking.meeting_url in content
    assert precheck_url(booking.meeting_url) in content
    assert "Sprawdź kamerę i mikrofon" in content


# --- (c) przypomnienie dobę wcześniej -------------------------------------------------------------


def test_reminder_goes_once_for_tomorrow(stage, participant, django_capture_on_commit_callbacks):
    slot = make_slot(stage, minutes_ahead=20 * 60)  # ~20 godzin, czyli w oknie doby
    booking = book_slot(participant, slot)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        sent = remind_interviews()

    booking.refresh_from_db()
    assert sent == 1
    assert booking.reminder_sent_at is not None
    message = mail.outbox[-1]
    assert message.to == [participant.user.email]
    assert booking.meeting_url in message.body

    # Drugi przebieg tego samego dnia nie może dołożyć drugiego listu.
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert remind_interviews() == 0
    assert mail.outbox == []


def test_reminder_skips_distant_and_started_interviews(stage, participant):
    distant = make_slot(stage, minutes_ahead=5 * 24 * 60)
    book_slot(participant, distant)

    assert remind_interviews() == 0

    # Rozmowa, która już trwa, też nie potrzebuje przypomnienia – okno jest półotwarte od „teraz”.
    InterviewSlot.objects.filter(pk=distant.pk).update(
        starts_at=timezone.now() - timedelta(minutes=5),
        ends_at=timezone.now() + timedelta(minutes=15),
    )
    assert remind_interviews() == 0
    assert not InterviewBooking.objects.filter(reminder_sent_at__isnull=False).exists()
