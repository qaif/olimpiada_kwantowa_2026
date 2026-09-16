"""Kalendarz osobisty uczestnika i plik iCalendar.

Plik ``.ics`` sprawdzamy **bez parsera**: asercje idą na to, co RFC 5545 nakazuje, a czego
najłatwiej nie dopilnować przy ręcznym składaniu tekstu – obecność kopert, format dat, wyłączny
``DTEND`` zakresu całodniowego, cytowanie przecinka i zawijanie długich wierszy. Wciągnięcie
biblioteki tylko po to, żeby sprawdzić własny generator, kazałoby wierzyć, że obie strony
rozumieją RFC tak samo – a to jest dokładnie ta rzecz, którą test ma rozstrzygnąć.
"""

from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.cms.calendar import (
    ICS_FILENAME,
    PRODID,
    CalendarItem,
    calendar_ics,
    escape_text,
    participant_calendar,
)
from apps.competitions.models import StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    InterviewBookingFactory,
    InterviewSlotFactory,
    InterviewStageFactory,
    StageEntryFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

ICS_URL = "/me/calendar.ics"
CALENDAR_URL = "/me/calendar/"


def lines(text: str) -> list[str]:
    return text.split("\r\n")


def unfolded(text: str) -> str:
    """Skleja wiersze kontynuacji – tak, jak zrobiłby to czytnik kalendarza (RFC 5545 §3.1)."""
    return text.replace("\r\n ", "")


@pytest.fixture
def edition():
    return CurrentEditionFactory()


@pytest.fixture
def participant():
    return ParticipantFactory()


# --- lista terminów ------------------------------------------------------------------------------


def test_calendar_carries_stages_of_the_current_edition(edition, participant):
    stage = StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I – eliminacje")

    titles = [item.title for item in participant_calendar(participant, edition=edition)]

    assert stage.display_name in titles


def test_calendar_carries_the_own_interview_slot(edition, participant):
    stage = InterviewStageFactory(edition=edition, name="Etap II – rozmowy")
    slot = InterviewSlotFactory(stage=stage, note="komisja A")
    entry = StageEntryFactory(participant=participant, stage=stage, status=StageEntryStatus.QUALIFIED)
    InterviewBookingFactory(slot=slot, entry=entry)

    items = participant_calendar(participant, edition=edition)
    interview = next(item for item in items if item.kind == "interview")

    assert interview.title == f"Rozmowa kwalifikacyjna: {stage.display_name}"
    assert interview.is_all_day is False
    assert interview.starts_at == slot.starts_at


def test_someone_elses_interview_is_not_in_my_calendar(edition, participant):
    stage = InterviewStageFactory(edition=edition)
    slot = InterviewSlotFactory(stage=stage, capacity=2)
    other = StageEntryFactory(participant=ParticipantFactory(), stage=stage)
    InterviewBookingFactory(slot=slot, entry=other)

    items = participant_calendar(participant, edition=edition)

    assert [item for item in items if item.kind == "interview"] == []


def test_calendar_is_sorted_by_start_date(edition, participant):
    later = StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        name="Finał",
        opens_at=timezone.now() + timedelta(days=200),
    )
    earlier = StageFactory(edition=edition, kind=StageKind.ELIM, name="Eliminacje")

    titles = [item.title for item in participant_calendar(participant, edition=edition)]

    assert titles.index(earlier.display_name) < titles.index(later.display_name)


def test_no_current_edition_means_an_empty_calendar(participant):
    assert participant_calendar(participant, edition=None) == []


# --- plik iCalendar -------------------------------------------------------------------------------


def all_day(title="Finał", start=date(2027, 6, 4), end=date(2027, 6, 7), **kwargs) -> CalendarItem:
    return CalendarItem(kind="stage", title=title, start=start, end=end, **kwargs)


def test_ics_has_the_required_envelope():
    text = calendar_ics([all_day()])

    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert f"PRODID:{PRODID}" in lines(text)
    assert "VERSION:2.0" in lines(text)
    assert "BEGIN:VEVENT" in lines(text)
    assert "END:VEVENT" in lines(text)


def test_empty_calendar_is_still_a_valid_file():
    """Subskrybent pustego kalendarza ma zobaczyć pusty kalendarz, a nie błąd pobierania."""
    text = calendar_ics([])

    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert "BEGIN:VEVENT" not in text


def test_all_day_event_uses_dates_with_an_exclusive_end():
    """RFC 5545: ``DTEND`` zakresu dat jest **wyłączny**, więc 4–7 czerwca kończy się ósmego."""
    text = calendar_ics([all_day()])

    assert "DTSTART;VALUE=DATE:20270604" in lines(text)
    assert "DTEND;VALUE=DATE:20270608" in lines(text)


def test_timed_event_is_written_in_utc():
    start = timezone.make_aware(datetime(2027, 3, 12, 10, 0))
    item = CalendarItem(
        kind="interview",
        title="Rozmowa",
        start=start.date(),
        end=start.date(),
        starts_at=start,
        ends_at=start + timedelta(minutes=20),
    )

    text = calendar_ics([item])

    assert [line for line in lines(text) if line.startswith("DTSTART:")][0].endswith("Z")
    assert [line for line in lines(text) if line.startswith("DTEND:")][0].endswith("Z")


def test_uid_is_stable_between_downloads():
    first = calendar_ics([all_day()])
    second = calendar_ics([all_day()])

    uids = [line for line in lines(first) if line.startswith("UID:")]
    assert uids
    assert uids == [line for line in lines(second) if line.startswith("UID:")]


def test_uid_differs_between_events():
    text = calendar_ics([all_day(title="Finał"), all_day(title="Gala")])

    uids = {line for line in lines(text) if line.startswith("UID:")}
    assert len(uids) == 2


def test_comma_and_semicolon_are_escaped():
    text = unfolded(calendar_ics([all_day(title="Finał, Kraków; Wydział Fizyki")]))

    assert "SUMMARY:Finał\\, Kraków\\; Wydział Fizyki" in text


def test_backslash_is_escaped_before_anything_else():
    assert escape_text("a\\b,c") == "a\\\\b\\,c"


def test_newline_becomes_an_escaped_n():
    assert escape_text("pierwszy\ndrugi") == "pierwszy\\ndrugi"


def test_long_line_is_folded_to_75_octets():
    text = calendar_ics([all_day(title="Zjazd finałowy " + "ą" * 120)])

    for line in lines(text):
        assert len(line.encode("utf-8")) <= 75, line
    # Zawinięcie nie może zgubić treści – po sklejeniu tytuł wraca w całości.
    assert "ą" * 120 in unfolded(text)


def test_relative_url_is_not_written_as_a_url_property():
    """„/warsztaty/” nie jest URI, którym kalendarz umie cokolwiek otworzyć."""
    text = calendar_ics([all_day(url="/warsztaty/"), all_day(title="Gala", url="https://example.test/")])

    assert "URL:/warsztaty/" not in unfolded(text)
    assert "URL:https://example.test/" in unfolded(text)


# --- widoki ---------------------------------------------------------------------------------------


def test_calendar_page_is_participants_only(web_client, edition, participant):
    web_client.force_login(participant.user)

    assert web_client.get(CALENDAR_URL).status_code == 200
    web_client.logout()
    assert web_client.get(CALENDAR_URL).status_code == 302


def test_ics_response_has_type_and_disposition(web_client, edition, participant):
    StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I")
    web_client.force_login(participant.user)

    response = web_client.get(ICS_URL)
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="{ICS_FILENAME}"'
    assert body.startswith("BEGIN:VCALENDAR")
    assert "Etap I" in unfolded(body)
