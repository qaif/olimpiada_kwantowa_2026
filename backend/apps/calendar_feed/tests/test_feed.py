"""Kanał ``/kalendarz.ics`` z bazą: źródła terminów, izolacja konkursów, nagłówki, pamięć, przyciski.

Treść sprawdzamy czytnikiem z ``ics_parser`` (rozwinięcie wierszy, koperty, cytowanie), a nie
szukaniem napisów w surowym pliku – patrz docstring ``test_ics``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone
from wagtail.models import Page

from apps.calendar_feed.views import PublicCalendarFeedView
from apps.cms.models import ContentPage, HomePage
from apps.cms.workshops import WORKSHOPS_SLUG
from apps.competitions.models import EditionEvent, StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory

from .ics_parser import parse, unescape

pytestmark = pytest.mark.django_db

FEED_URL = "/kalendarz.ics"

#: Napisy na tyle nietypowe, że trafienie w pliku nie może być przypadkiem (wzorzec z
#: ``apps/cms/tests/test_competition_scope.py``).
OWN_TOPIC = "Kubity, bramki; pomiary"
OTHER_TOPIC = "Temat obcego organizatora Zebra"


def moment(year, month, day, hour=9, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def stage(edition, kind=StageKind.ELIM, *, opens_at, deadline_at, **extra):
    """Etap o podanym oknie – terminy recenzji i reklamacji liczone od deadline'u (więzy w bazie)."""
    return StageFactory(
        edition=edition,
        kind=kind,
        opens_at=opens_at,
        deadline_at=deadline_at,
        review_deadline_at=deadline_at + timedelta(days=14),
        appeal_window_opens_at=deadline_at + timedelta(days=16),
        appeal_window_closes_at=deadline_at + timedelta(days=23),
        **extra,
    )


def workshops_page(parent, rows: list[dict]) -> ContentPage:
    """Strona „Warsztaty” z jednym blokiem ``schedule`` – kształt jak w ``test_timeline_strip``."""
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "Godziny",
                    "lecturer_label": "Prowadzący",
                    "rows": [{"date": "słownie", "time": "", "lecturer": "", **row} for row in rows],
                },
            )
        ],
    )
    parent.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def republish(page: ContentPage, rows: list[dict]) -> None:
    """Nowa wersja tabeli – tak, jak zapisuje ją redaktor w ``/cms/``."""
    page.body = [
        (
            "schedule",
            {
                "caption": "",
                "topic_label": "Temat",
                "date_label": "Termin",
                "time_label": "Godziny",
                "lecturer_label": "Prowadzący",
                "rows": [{"date": "słownie", "time": "", "lecturer": "", **row} for row in rows],
            },
        )
    ]
    page.save_revision().publish()


KUBITY = {
    "topic": OWN_TOPIC,
    "date_value": date(2026, 11, 21),
    "time": "11:00–13:00",
    "lecturer": "Krzysztof Pawłowski",
}


@pytest.fixture
def home_page() -> HomePage:
    return HomePage.objects.get()


@pytest.fixture
def other_home(other_competition) -> HomePage:
    """Drzewo stron drugiego konkursu – ten sam układ, co w ``test_competition_scope``."""
    tree_root = Page.objects.filter(depth=1).order_by("path").first()
    home = tree_root.add_child(instance=HomePage(title="Druga olimpiada", slug="druga-olimpiada"))
    site = other_competition.site
    site.root_page = home
    site.save()
    return home


def feed(client) -> tuple:
    response = client.get(FEED_URL)
    assert response.status_code == 200
    return response, parse(response.content.decode("utf-8"))


def events_by_summary(calendar) -> dict:
    return {unescape(event.get("SUMMARY")): event for event in calendar.all("VEVENT")}


# --- warsztaty ---------------------------------------------------------------------------------------


def test_workshop_has_local_hours_lecturer_and_link_but_no_meeting(client_for, competition, home_page):
    workshops_page(home_page, [KUBITY])

    response, calendar = feed(client_for(competition))
    event = events_by_summary(calendar)[f"Warsztaty: {OWN_TOPIC}"]

    assert event.params("DTSTART") == {"TZID": "Europe/Warsaw"}
    assert event.get("DTSTART") == "20261121T110000"
    assert event.get("DTEND") == "20261121T130000"
    description = unescape(event.get("DESCRIPTION"))
    assert "Prowadzący: Krzysztof Pawłowski" in description
    assert f"/{WORKSHOPS_SLUG}/" in description
    assert event.get("URL").endswith(f"/{WORKSHOPS_SLUG}/")
    assert event.get("URL").startswith("http")
    assert event.get("LAST-MODIFIED")
    assert int(event.get("SEQUENCE")) > 0
    assert "meet.google" not in response.content.decode("utf-8")
    assert calendar.all("VTIMEZONE")[0].get("TZID") == "Europe/Warsaw"


def test_workshop_without_hours_is_all_day_and_without_date_is_skipped(client_for, competition, home_page):
    workshops_page(
        home_page,
        [
            {"topic": "Bez godzin", "date_value": date(2026, 12, 5), "time": "do potwierdzenia"},
            {"topic": "Bez daty", "date_value": None},
        ],
    )

    _response, calendar = feed(client_for(competition))
    events = events_by_summary(calendar)

    assert events["Warsztaty: Bez godzin"].get("DTSTART") == "20261205"
    assert events["Warsztaty: Bez godzin"].params("DTSTART") == {"VALUE": "DATE"}
    assert "Warsztaty: Bez daty" not in events


def test_workshop_uid_is_stable_and_survives_a_change_of_hours(client_for, competition, home_page):
    page = workshops_page(home_page, [KUBITY])
    client = client_for(competition)
    _r, first = feed(client)
    _r, again = feed(client)
    [before] = first.all("VEVENT")

    republish(page, [{**KUBITY, "time": "12:00–14:00", "lecturer": "Ktoś inny"}])
    _r, changed = feed(client)
    [after] = changed.all("VEVENT")

    assert again.all("VEVENT")[0].get("UID") == before.get("UID")
    assert after.get("UID") == before.get("UID")
    assert before.get("UID").startswith(f"workshop-c{competition.pk}-2026-11-21-")
    assert after.get("DTSTART") == "20261121T120000"
    assert int(after.get("SEQUENCE")) >= int(before.get("SEQUENCE"))


# --- terminy edycji ---------------------------------------------------------------------------------


def test_stage_opening_and_deadline_are_moments_in_local_time(client_for, competition):
    edition = CurrentEditionFactory()
    elim = stage(
        edition, opens_at=moment(2026, 11, 1, 0, 0), deadline_at=moment(2026, 12, 15, 23, 59), name="Etap I"
    )
    stage(
        edition,
        StageKind.TRAINING,
        opens_at=moment(2026, 9, 1),
        deadline_at=moment(2026, 9, 30),
        name="Trening ukryty",
    )

    _r, calendar = feed(client_for(competition))
    events = events_by_summary(calendar)

    opening = events["Etap I: otwarcie"]
    deadline = events["Etap I: termin oddania prac"]
    assert opening.get("UID") == f"stage-{elim.pk}-opens@olimpiadakwantowa.pl"
    assert opening.get("DTSTART") == "20261101T000000"
    assert deadline.get("DTSTART") == "20261215T235900"
    assert deadline.get("DTEND") is None
    assert not any("Trening ukryty" in summary for summary in events)


def test_onsite_stage_is_an_all_day_event_with_location(client_for, competition):
    edition = CurrentEditionFactory()
    stage(
        edition,
        StageKind.FINAL,
        opens_at=moment(2027, 6, 5, 9),
        deadline_at=moment(2027, 6, 5, 14),
        name="Finał",
        location="Kraków, Wydział Fizyki",
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    _r, calendar = feed(client_for(competition))
    final = events_by_summary(calendar)["Finał"]

    assert final.get("DTSTART") == "20270604"
    assert final.get("DTEND") == "20270608"
    assert unescape(final.get("LOCATION")) == "Kraków, Wydział Fizyki"


def test_registration_window_and_coordinator_events(client_for, competition):
    edition = CurrentEditionFactory(
        registration_enabled=True,
        registration_opens_at=moment(2026, 9, 1, 8),
        registration_closes_at=moment(2026, 10, 31, 23, 59),
    )
    EditionEvent.objects.create(
        edition=edition,
        title="Gala",
        starts_on=date(2027, 6, 12),
        note="Wejście https://meet.google.com/abc-defg-hij",
        url="https://meet.google.com/abc-defg-hij",
    )
    EditionEvent.objects.create(
        edition=edition, title="Ukryte", starts_on=date(2027, 6, 13), show_on_timeline=False
    )

    response, calendar = feed(client_for(competition))
    events = events_by_summary(calendar)

    assert events["Rejestracja uczestników: start"].get("DTSTART") == "20260901T080000"
    assert events["Rejestracja uczestników: koniec"].get("DTSTART") == "20261031T235900"
    assert events["Gala"].get("DTSTART") == "20270612"
    assert events["Gala"].get("URL") is None
    assert "Ukryte" not in events
    assert "meet.google" not in response.content.decode("utf-8").replace("\r\n ", "")


# --- izolacja konkursów -------------------------------------------------------------------------------


def test_each_competition_sees_only_its_own_workshops_and_stages(
    client_for, competition, other_competition, home_page, other_home
):
    workshops_page(home_page, [KUBITY])
    workshops_page(
        other_home, [{"topic": OTHER_TOPIC, "date_value": date(2026, 11, 21), "time": "11:00–13:00"}]
    )
    other_edition = CurrentEditionFactory(competition=other_competition)
    stage(
        other_edition,
        opens_at=moment(2026, 11, 1),
        deadline_at=moment(2026, 12, 1),
        name="Etap obcy Zebra",
        competition=other_competition,
    )

    own = client_for(competition).get(FEED_URL).content.decode("utf-8").replace("\r\n ", "")
    other = client_for(other_competition).get(FEED_URL).content.decode("utf-8").replace("\r\n ", "")

    assert "Kubity" in own
    assert "Zebra" not in own
    assert "Zebra" in other
    assert "Kubity" not in other
    own_uids = {event.get("UID") for event in parse(own).all("VEVENT")}
    other_uids = {event.get("UID") for event in parse(other).all("VEVENT")}
    assert not own_uids & other_uids


# --- HTTP ------------------------------------------------------------------------------------------------


def test_headers_type_disposition_cache_and_etag(client_for, competition):
    response = client_for(competition).get(FEED_URL)

    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    assert response["Content-Disposition"].startswith("inline; filename=")
    assert response["Content-Disposition"].endswith('.ics"')
    assert "public" in response["Cache-Control"]
    assert "max-age=900" in response["Cache-Control"]
    assert response["ETag"].startswith('"')


def test_matching_etag_gives_304(client_for, competition):
    client = client_for(competition)
    etag = client.get(FEED_URL)["ETag"]

    response = client.get(FEED_URL, HTTP_IF_NONE_MATCH=etag)

    assert response.status_code == 304
    assert response.content == b""


def test_no_competition_in_the_request_is_404():
    from django.http import Http404

    request = RequestFactory().get(FEED_URL)
    request.competition = None

    with pytest.raises(Http404):
        PublicCalendarFeedView.as_view()(request)


def test_cached_feed_is_invalidated_by_publishing_the_workshops_page(
    client_for, competition, home_page, settings
):
    settings.PAGE_CACHE_ENABLED = True
    settings.PAGE_CACHE_SECONDS = 120
    page = workshops_page(home_page, [KUBITY])
    client = client_for(competition)
    assert "Kubity" in client.get(FEED_URL).content.decode("utf-8").replace("\r\n ", "")

    republish(page, [{**KUBITY, "topic": "Nowy temat po publikacji"}])
    body = client.get(FEED_URL).content.decode("utf-8").replace("\r\n ", "")

    assert "Nowy temat po publikacji" in body
    assert "Kubity" not in body


# --- przyciski na stronach ------------------------------------------------------------------------------


def test_workshops_page_has_subscribe_buttons_and_per_workshop_links(client_for, competition, home_page):
    workshops_page(home_page, [KUBITY])

    html = client_for(competition).get(f"/{WORKSHOPS_SLUG}/").content.decode("utf-8")

    assert "https://calendar.google.com/calendar/r?cid=webcal%3A%2F%2F" in html
    assert 'href="webcal://' in html
    assert "/kalendarz.ics" in html
    assert "calendar.google.com/calendar/render?action=TEMPLATE" in html


def test_participant_calendar_screen_has_subscribe_buttons(client_for, competition):
    from apps.accounts.tests.factories import ParticipantFactory

    CurrentEditionFactory()
    participant = ParticipantFactory()
    client = client_for(competition)
    client.force_login(participant.user)

    html = client.get("/me/calendar/").content.decode("utf-8")

    assert 'href="webcal://' in html
    assert "calendar.google.com/calendar/r?cid=" in html
