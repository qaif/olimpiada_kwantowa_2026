"""Plik iCalendar kanału publicznego bez bazy: strefa i zmiana czasu, zawijanie, cytowanie, godziny.

Wszystkie asercje idą przez czytnik z ``ics_parser`` – ten sam porządek, w którym plik czyta klient
kalendarza – a nie przez szukanie napisów w surowym tekście: wiersz zawinięty w środku słowa jest
poprawny, a test szukający „Warsztaty: Kubity” w surowym pliku by go odrzucił.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest

from apps.calendar_feed.ics import (
    FeedEvent,
    etag_for,
    google_event_url,
    google_subscribe_url,
    is_meeting_url,
    parse_hours,
    render_calendar,
    sequence_for,
    strip_meeting_links,
    vtimezone_lines,
    webcal_url,
)

from .ics_parser import parse, unescape, unfold

WARSAW = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def render(events, zone=WARSAW, **kwargs) -> str:
    return render_calendar(
        events, zone=zone, name=kwargs.pop("name", "Olimpiada Kwantowa"), prodid="-//Test//PL", now=NOW
    )


def workshop(**overrides) -> FeedEvent:
    values = {
        "uid": "workshop-c1-2026-11-21-kubity@olimpiadakwantowa.pl",
        "summary": "Warsztaty: Kubity, bramki; pomiary",
        "description": "Prowadzący: Krzysztof Pawłowski\nSzczegóły: https://olimpiadakwantowa.pl/warsztaty/",
        "location": "online",
        "url": "https://olimpiadakwantowa.pl/warsztaty/",
        "starts_at": datetime(2026, 11, 21, 11, 0),
        "ends_at": datetime(2026, 11, 21, 13, 0),
    }
    values.update(overrides)
    return FeedEvent(**values)


# --- koperta i parsowalność -------------------------------------------------------------------------


def test_the_file_parses_and_has_the_required_envelope():
    calendar = parse(render([workshop()]))

    assert calendar.get("VERSION") == "2.0"
    assert calendar.get("PRODID")
    assert calendar.get("METHOD") == "PUBLISH"
    assert calendar.get("X-WR-TIMEZONE") == "Europe/Warsaw"
    [event] = calendar.all("VEVENT")
    for required in ("UID", "DTSTAMP", "DTSTART", "SUMMARY"):
        assert event.get(required), required
    assert event.get("DTSTAMP") == "20261018T120000Z"


def test_an_empty_calendar_is_still_a_valid_file():
    calendar = parse(render([]))

    assert calendar.all("VEVENT") == []
    # Bez godzin w pliku blok strefy nie jest potrzebny (RFC wymaga go tylko dla użytego TZID).
    assert calendar.all("VTIMEZONE") == []


# --- strefa czasowa i zmiana czasu -------------------------------------------------------------------


def test_timed_events_use_local_time_with_tzid_and_a_matching_vtimezone():
    calendar = parse(render([workshop()]))
    [event] = calendar.all("VEVENT")
    [zone] = calendar.all("VTIMEZONE")

    assert event.params("DTSTART") == {"TZID": "Europe/Warsaw"}
    assert event.get("DTSTART") == "20261121T110000"
    assert event.get("DTEND") == "20261121T130000"
    assert zone.get("TZID") == "Europe/Warsaw"


def test_vtimezone_has_both_dst_transitions_of_the_year():
    lines = vtimezone_lines(WARSAW, 2026, 2026)
    calendar = parse("\r\n".join(["BEGIN:VCALENDAR", *lines, "END:VCALENDAR"]) + "\r\n")
    [zone] = calendar.all("VTIMEZONE")

    daylight = zone.all("DAYLIGHT")
    standard = zone.all("STANDARD")
    # Początek roku (CET) + koniec października (CEST → CET) i ostatnia niedziela marca (CET → CEST).
    assert [item.get("DTSTART") for item in daylight] == ["20260329T020000"]
    assert [(item.get("TZOFFSETFROM"), item.get("TZOFFSETTO")) for item in daylight] == [("+0100", "+0200")]
    assert [item.get("TZNAME") for item in daylight] == ["CEST"]
    assert [item.get("DTSTART") for item in standard] == ["20260101T010000", "20261025T030000"]
    assert standard[-1].get("TZOFFSETFROM") == "+0200"
    assert standard[-1].get("TZOFFSETTO") == "+0100"


def test_vtimezone_covers_every_year_with_events():
    calendar = parse(
        render([workshop(), workshop(uid="b@x", starts_at=datetime(2027, 3, 27, 11), ends_at=None)])
    )
    [zone] = calendar.all("VTIMEZONE")

    starts = [item.get("DTSTART") for item in zone.children]
    assert "20270328T020000" in starts  # zmiana czasu 2027 – dzień po warsztacie
    assert "20271031T030000" in starts


def test_wall_clock_survives_the_october_change():
    """Warsztat 24.10 i 31.10 o 11:00 – po obu stronach zmiany czasu – zostaje o 11:00 w pliku."""
    before = workshop(uid="a@x", starts_at=datetime(2026, 10, 24, 11), ends_at=datetime(2026, 10, 24, 13))
    after = workshop(uid="b@x", starts_at=datetime(2026, 10, 31, 11), ends_at=datetime(2026, 10, 31, 14))
    events = parse(render([before, after])).all("VEVENT")

    assert [event.get("DTSTART") for event in events] == ["20261024T110000", "20261031T110000"]
    # A w UTC to są różne godziny – i dokładnie dlatego nie zapisujemy ich jako ``…Z``.
    assert datetime(2026, 10, 24, 11, tzinfo=WARSAW).utcoffset() == timedelta(hours=2)
    assert datetime(2026, 10, 31, 11, tzinfo=WARSAW).utcoffset() == timedelta(hours=1)


def test_a_zone_without_dst_gets_a_single_observance():
    lines = vtimezone_lines(ZoneInfo("Asia/Kolkata"), 2026, 2027)

    assert lines.count("BEGIN:STANDARD") == 1
    assert "BEGIN:DAYLIGHT" not in lines
    assert "TZOFFSETTO:+0530" in lines


def test_all_day_events_have_an_exclusive_dtend():
    final = FeedEvent(
        uid="stage-9-event@x", summary="Finał", start_date=date(2027, 6, 4), end_date=date(2027, 6, 7)
    )
    [event] = parse(render([final])).all("VEVENT")

    assert event.params("DTSTART") == {"VALUE": "DATE"}
    assert event.get("DTSTART") == "20270604"
    assert event.get("DTEND") == "20270608"
    assert event.get("TRANSP") == "TRANSPARENT"


def test_a_deadline_is_a_moment_without_dtend():
    deadline = FeedEvent(
        uid="stage-1-deadline@x", summary="Etap I: termin", starts_at=datetime(2026, 12, 15, 23, 59)
    )
    [event] = parse(render([deadline])).all("VEVENT")

    assert event.get("DTSTART") == "20261215T235900"
    assert event.get("DTEND") is None
    assert event.get("TRANSP") == "TRANSPARENT"


# --- zawijanie i cytowanie ---------------------------------------------------------------------------


def test_lines_are_folded_to_75_octets_without_splitting_polish_characters():
    long_topic = (
        "Warsztaty: " + "Podstawy mechaniki kwantowej i podstawowe układy kwantowe – żółć, źdźbło, ęą " * 3
    )
    text = render([workshop(summary=long_topic, description="ąęśćżźńół" * 30)])

    physical = text[:-2].split("\r\n")
    assert any(line.startswith(" ") for line in physical), "długie wiersze mają być zawinięte"
    for line in physical:
        assert len(line.encode("utf-8")) <= 75, line
    [event] = parse(text).all("VEVENT")
    # Sklejone z powrotem dają dokładnie ten sam tekst – czyli żaden znak nie został przecięty.
    assert unescape(event.get("SUMMARY")) == long_topic.strip()
    assert unescape(event.get("DESCRIPTION")) == "ąęśćżźńół" * 30


def test_text_values_escape_backslash_semicolon_comma_and_newlines():
    event = workshop(summary="A,B;C\\D", description="Prowadzący: Jan\nDruga linia\r\nTrzecia")
    [parsed] = parse(render([event])).all("VEVENT")

    assert parsed.get("SUMMARY") == "A\\,B\\;C\\\\D"
    assert unescape(parsed.get("SUMMARY")) == "A,B;C\\D"
    assert unescape(parsed.get("DESCRIPTION")) == "Prowadzący: Jan\nDruga linia\nTrzecia"


def test_polish_characters_survive_unchanged():
    [parsed] = parse(render([workshop(summary="Warsztaty: Splątanie i twierdzenie Bella – źdźbło")])).all(
        "VEVENT"
    )

    assert unescape(parsed.get("SUMMARY")) == "Warsztaty: Splątanie i twierdzenie Bella – źdźbło"


def test_the_calendar_name_is_escaped():
    calendar = parse(render([], name="Olimpiada, edycja; I"))

    assert unescape(calendar.get("X-WR-CALNAME")) == "Olimpiada, edycja; I"


def test_url_is_a_uri_not_escaped_text():
    [parsed] = parse(render([workshop(url="https://example.org/a,b?x=1;y=2")])).all("VEVENT")

    assert parsed.get("URL") == "https://example.org/a,b?x=1;y=2"


def test_url_with_a_line_break_is_dropped():
    [parsed] = parse(render([workshop(url="https://example.org/\r\nX-EVIL:1")])).all("VEVENT")

    assert parsed.get("URL") is None
    assert parsed.get("X-EVIL") is None


# --- bez linku do spotkania ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.google.com/abc-defg-hij",
        "https://us02web.zoom.us/j/123",
        "https://teams.microsoft.com/l/meetup-join/x",
        "https://meet.jit.si/olimpiada",
        "meet.google.com/abc-defg-hij",
    ],
)
def test_meeting_urls_are_recognised(url):
    assert is_meeting_url(url)


def test_ordinary_urls_are_not_meeting_urls():
    assert not is_meeting_url("https://olimpiadakwantowa.pl/warsztaty/")
    assert not is_meeting_url("https://meet.google.com.evil.example/")


def test_meeting_links_never_reach_the_file():
    event = workshop(
        summary="Warsztaty https://meet.google.com/abc-defg-hij",
        description="Wejście: https://meet.google.com/abc-defg-hij\nSzczegóły: https://olimpiadakwantowa.pl/warsztaty/",
        location="https://zoom.us/j/1",
        url="https://meet.google.com/abc-defg-hij",
    )
    text = render([event])

    assert "meet.google" not in text.replace("\r\n ", "")
    assert "zoom.us" not in text.replace("\r\n ", "")
    assert "olimpiadakwantowa.pl/warsztaty/" in text.replace("\r\n ", "")


def test_strip_meeting_links_keeps_other_text():
    assert (
        strip_meeting_links("Link: https://meet.google.com/x a reszta zostaje") == "Link:  a reszta zostaje"
    )


# --- godziny z harmonogramu ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11:00–14:00", (time(11), time(14))),
        ("11.00-13.30", (time(11), time(13, 30))),
        ("od 17:00 do 18:30", (time(17), time(18, 30))),
        ("9:15 — 10:00", (time(9, 15), time(10))),
        ("godz. 18:00", (time(18), None)),
        ("14:00–11:00", (time(14), None)),
        ("do potwierdzenia", None),
        ("", None),
        ("25:00", None),
    ],
)
def test_parse_hours(text, expected):
    assert parse_hours(text) == expected


# --- UID, SEQUENCE, ETag ---------------------------------------------------------------------------------


def test_sequence_grows_with_the_last_change():
    first = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)

    assert sequence_for(None) == 0
    assert sequence_for(datetime(2025, 1, 1, tzinfo=UTC)) == 0
    assert sequence_for(first) < sequence_for(first + timedelta(seconds=30))
    assert sequence_for(first) < 2**31


def test_last_modified_and_sequence_are_written():
    modified = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)
    [parsed] = parse(render([workshop(last_modified=modified)])).all("VEVENT")

    assert parsed.get("LAST-MODIFIED") == "20261017T093000Z"
    assert int(parsed.get("SEQUENCE")) == sequence_for(modified)


def test_etag_ignores_dtstamp_but_not_content():
    one = render_calendar([workshop()], zone=WARSAW, name="X", prodid="P", now=NOW)
    later = render_calendar([workshop()], zone=WARSAW, name="X", prodid="P", now=NOW + timedelta(hours=1))
    changed = render_calendar([workshop(summary="Inny")], zone=WARSAW, name="X", prodid="P", now=NOW)

    assert one != later
    assert etag_for(one) == etag_for(later)
    assert etag_for(one) != etag_for(changed)
    assert etag_for(one).startswith('"') and etag_for(one).endswith('"')


# --- odnośniki do klientów ----------------------------------------------------------------------------


def test_subscription_links():
    feed = "https://olimpiadakwantowa.pl/kalendarz.ics"

    assert webcal_url(feed) == "webcal://olimpiadakwantowa.pl/kalendarz.ics"
    google = google_subscribe_url(feed)
    assert google.startswith("https://calendar.google.com/calendar/r?cid=")
    assert unquote(google.split("cid=", 1)[1]) == "webcal://olimpiadakwantowa.pl/kalendarz.ics"


def test_google_template_for_a_timed_workshop():
    url = google_event_url(workshop(), WARSAW)
    query = parse_qs(urlsplit(url).query)

    assert urlsplit(url).netloc == "calendar.google.com"
    assert query["action"] == ["TEMPLATE"]
    assert query["dates"] == ["20261121T110000/20261121T130000"]
    assert query["ctz"] == ["Europe/Warsaw"]
    assert query["text"] == ["Warsztaty: Kubity, bramki; pomiary"]
    assert "Krzysztof Pawłowski" in query["details"][0]


def test_google_template_for_an_all_day_workshop_drops_meeting_links():
    event = workshop(
        starts_at=None,
        ends_at=None,
        start_date=date(2026, 11, 21),
        end_date=date(2026, 11, 21),
        description="https://meet.google.com/x",
    )
    query = parse_qs(urlsplit(google_event_url(event, WARSAW)).query)

    assert query["dates"] == ["20261121/20261122"]
    assert "details" not in query


def test_unfold_rejects_a_bare_lf():
    with pytest.raises(AssertionError):
        unfold("BEGIN:VCALENDAR\nEND:VCALENDAR\r\n")
