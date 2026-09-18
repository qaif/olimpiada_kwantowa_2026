"""Ekrany logistyki: miejsca zawodów, przyjazdy i potrzeby, obecność.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- trzy adresy są **wyłączone** bez flagi ``onsite_logistics`` i dają wtedy 404, a nie 403 (§ 2.1);
  Olimpiada Kwantowa ich nie włącza,
- **decyzja D21**: potrzeb szczególnych (dieta, dostępność) i uwagi tekstowej nie zbiera się
  domyślnie, a przełącznik zostawia wpis audytowy. Dopóki jest wyłączony, uwaga nie ma jak trafić
  ani na ekran koordynatora, ani na listę żywieniową – i dlatego test pilnuje **obu** kierunków:
  zapisu (serwis czyści) i odczytu (wiersz nie ma klucza),
- listy do druku są PDF-ami składanymi cudzą ścieżką (``render_logistics_list``), a rodzaj spoza
  zamkniętej listy daje 404,
- obecność notuje się po jednym wierszu, a godzinę stawia serwis – przy sporze o dopuszczenie do
  zawodów to ona jest odpowiedzią,
- etapu sąsiada nie da się wskazać identyfikatorem w adresie (§ 3.6).

Adresy są w ``apps/web/urls.py`` – patrz docstring ``test_coordinator_fees.py``.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.logistics import (
    FEATURE,
    ArrivalForm,
    AttendanceRecord,
    LogisticsNeed,
    Venue,
    create_venue,
    save_arrival_form,
    set_special_needs_collection,
)
from apps.competitions.tests.factories import EditionFactory, StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

VENUES_URL = "/coordinator/venues/"


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client, user


@pytest.fixture
def coordinator_client(client_for, competition):
    enable(competition)
    client, _user = coordinator_for(client_for, competition)
    return client


@pytest.fixture
def venue(competition):
    enable(competition)
    return create_venue(competition, name="Wydział Fizyki", city="Kraków")


def logistics_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/logistics/"


def attendance_url(stage) -> str:
    return f"/coordinator/stages/{stage.pk}/attendance/"


# --- przełącznik ekranów --------------------------------------------------------------------------


def test_screens_are_off_for_a_competition_with_default_flags(client_for, competition, elim_stage):
    client, _user = coordinator_for(client_for, competition)

    assert not competition.has_feature(FEATURE)
    assert client.get(VENUES_URL).status_code == 404
    assert client.get(logistics_url(elim_stage)).status_code == 404
    assert client.get(attendance_url(elim_stage)).status_code == 404
    assert client.post(VENUES_URL, {"name": "Sala"}).status_code == 404
    assert Venue.objects.count() == 0


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory().user)

    assert client.get(VENUES_URL).status_code == 403

    enable(competition)

    assert client.get(VENUES_URL).status_code == 403


# --- miejsca zawodów ------------------------------------------------------------------------------


def test_venue_is_created_from_the_screen(coordinator_client, competition):
    response = coordinator_client.post(
        VENUES_URL, {"name": "Wydział Fizyki", "city": "Kraków", "address": "Łojasiewicza 11"}
    )

    assert response.status_code == 302
    row = Venue.objects.get(competition=competition)
    assert row.city == "Kraków"
    # Pusta pojemność zostaje ``None``: „nie wiadomo” i „zero miejsc” to dwie różne odpowiedzi.
    assert row.capacity is None


def test_second_venue_of_the_same_name_is_refused(coordinator_client, venue):
    response = coordinator_client.post(VENUES_URL, {"name": venue.name})

    assert response.status_code == 400
    assert Venue.objects.filter(name=venue.name).count() == 1


def test_venue_is_edited_from_the_screen(coordinator_client, venue):
    coordinator_client.post(
        f"{VENUES_URL}{venue.pk}/", {"name": venue.name, "city": "Warszawa", "capacity": "120"}
    )

    venue.refresh_from_db()
    assert venue.city == "Warszawa"
    assert venue.capacity == 120


def test_foreign_venue_is_404(client_for, competition, other_competition):
    enable(other_competition)
    foreign = create_venue(other_competition, name="Cudza sala")
    enable(competition)
    client, _user = coordinator_for(client_for, competition)

    response = client.post(f"{VENUES_URL}{foreign.pk}/", {"name": "Podmieniona"})

    foreign.refresh_from_db()
    assert response.status_code == 404
    assert foreign.name == "Cudza sala"


# --- decyzja D21 ----------------------------------------------------------------------------------


def test_special_needs_collection_is_off_by_default_and_warned_about(coordinator_client):
    content = coordinator_client.get(VENUES_URL).content.decode()

    assert "Nie podawaj diagnoz" in content
    assert "nie zbieramy" in content


def test_special_needs_switch_leaves_an_audit_entry(coordinator_client, competition):
    coordinator_client.post("/coordinator/venues/special-needs/", {"collect": "on"})

    from apps.competitions.logistics import collects_special_needs

    assert collects_special_needs(competition) is True
    assert AuditLog.objects.filter(action="logistics.special_needs_collection_changed").exists()


def test_note_is_invisible_while_the_collection_is_off(coordinator_client, competition, entry, elim_stage):
    """Uwaga zapisana przy włączonym zbieraniu nie wraca na ekran po jego wyłączeniu."""
    set_special_needs_collection(competition, enabled=True)
    save_arrival_form(entry, needs=[LogisticsNeed.DIET], note="dieta bezmięsna")
    set_special_needs_collection(competition, enabled=False)

    content = coordinator_client.get(logistics_url(elim_stage)).content.decode()

    assert "dieta bezmięsna" not in content


# --- przyjazdy i potrzeby -------------------------------------------------------------------------


def test_arrivals_screen_shows_rows_and_the_summary(
    coordinator_client, competition, entry, elim_stage, venue
):
    save_arrival_form(entry, venue=venue, needs=[LogisticsNeed.ACCOMMODATION, LogisticsNeed.MEAL])

    content = coordinator_client.get(logistics_url(elim_stage)).content.decode()

    assert entry.participant.public_code in content
    assert venue.name in content
    assert "nocleg" in content


def test_print_lists_are_pdfs(coordinator_client, entry, elim_stage, venue):
    save_arrival_form(entry, venue=venue, needs=[LogisticsNeed.ACCOMMODATION])

    for kind in ("attendance", "accommodation", "meal"):
        response = coordinator_client.get(f"{logistics_url(elim_stage)}{kind}/")

        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")


def test_unknown_list_kind_is_404(coordinator_client, elim_stage):
    assert coordinator_client.get(f"{logistics_url(elim_stage)}noclegowa/").status_code == 404


def test_foreign_stage_is_404(client_for, competition, other_competition):
    enable(other_competition)
    foreign_stage = StageFactory(edition=EditionFactory(competition=other_competition))
    enable(competition)
    client, _user = coordinator_for(client_for, competition)

    assert client.get(logistics_url(foreign_stage)).status_code == 404
    assert client.get(attendance_url(foreign_stage)).status_code == 404


# --- obecność -------------------------------------------------------------------------------------


def test_attendance_lists_every_entrant_not_only_the_checked_in(coordinator_client, entry, elim_stage):
    content = coordinator_client.get(attendance_url(elim_stage)).content.decode()

    assert entry.participant.public_code in content
    assert "nieodnotowany" in content


def test_attendance_is_recorded_with_a_time(coordinator_client, entry, elim_stage):
    coordinator_client.post(attendance_url(elim_stage), {"entry": entry.pk, "present": "1"})

    record = AttendanceRecord.objects.get(entry=entry)
    assert record.present is True
    assert record.checked_in_at is not None
    assert AuditLog.objects.filter(action="attendance.recorded").exists()


def test_attendance_can_be_taken_back(coordinator_client, entry, elim_stage):
    coordinator_client.post(attendance_url(elim_stage), {"entry": entry.pk, "present": "1"})
    coordinator_client.post(attendance_url(elim_stage), {"entry": entry.pk})

    record = AttendanceRecord.objects.get(entry=entry)
    assert record.present is False
    # Godzina przy nieobecnym opisywałaby zdarzenie, którego nie było.
    assert record.checked_in_at is None


def test_entry_of_another_stage_cannot_be_checked_in(coordinator_client, participant, edition, elim_stage):
    other_stage = StageFactory(edition=edition, kind="DISTRICT")
    foreign_entry = StageEntryFactory(participant=participant, stage=other_stage)

    response = coordinator_client.post(
        attendance_url(elim_stage), {"entry": foreign_entry.pk, "present": "1"}
    )

    assert response.status_code == 404
    assert not AttendanceRecord.objects.filter(entry=foreign_entry).exists()
    assert not ArrivalForm.objects.exists()
