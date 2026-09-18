"""Logistyka etapu stacjonarnego: flaga, dane szczególne (D21), izolacja i czynności.

Trzy rzeczy, których pilnują te testy, w kolejności wagi:

- **Konkurs #1 nie widzi niczego.** Bez flagi ``onsite_logistics`` listy są puste, zapis jest
  odmówiony, a rejestr czynności przetwarzania nie zmienia się o ani jeden wiersz. To jest warunek
  nadrzędny etapu 2 (``docs/UNIWERSALNY-ETAP-2.md`` § 0.1) w wydaniu na ten obszar.
- **Dane szczególne domyślnie nie istnieją** (decyzja organizatora D21, § 6): przy włączonej
  logistyce, ale bez świadomej zgody organizatora, „dieta” i „dostępność” nie są nawet do wyboru,
  a uwaga tekstowa nie zapisuje się, choćby przyszła z formularza.
- **Konkurs A nie widzi miejsc konkursu B** – reguła izolacji z § 5.7, sprawdzona na poziomie
  querysetu i na poziomie walidacji (miejsce z cudzego konkursu nie wchodzi do deklaracji).
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.processing_register import ACTIVITIES, activities_for, as_rows
from apps.competitions.logistics import (
    ArrivalForm,
    AttendanceRecord,
    LogisticsNeed,
    LogisticsSettings,
    Venue,
    arrival_rows,
    attendance_rows,
    available_needs,
    collects_special_needs,
    create_venue,
    need_labels,
    needs_summary,
    onsite_logistics_enabled,
    record_attendance,
    save_arrival_form,
    set_special_needs_collection,
    update_venue,
    validated_needs,
    venues_for,
)
from apps.core.api import DomainError

from .factories import StageEntryFactory, StageFactory

pytestmark = pytest.mark.django_db

FEATURE = "onsite_logistics"


def enable_logistics(competition):
    """Włącza flagę obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def onsite(competition):
    """Konkurs z włączoną logistyką i **bez** zgody na zbieranie danych szczególnych (stan domyślny)."""
    return enable_logistics(competition)


@pytest.fixture
def entry(onsite):
    """Wpis uczestnika do etapu stacjonarnego – korzeń wszystkich deklaracji w tych testach."""
    return StageEntryFactory(competition=onsite)


# --- flaga obszaru ---------------------------------------------------------------------------------


def test_competition_one_has_no_logistics(competition):
    """Domyślny stan Konkursu #1: obszaru nie ma, a lista miejsc jest pusta."""
    assert onsite_logistics_enabled(competition) is False
    assert list(venues_for(competition)) == []


def test_no_competition_means_no_logistics():
    """„Nie wiadomo, o który konkurs chodzi” to nie jest „flaga włączona”."""
    assert onsite_logistics_enabled(None) is False
    assert collects_special_needs(None) is False


def test_saving_an_arrival_form_without_the_flag_is_refused(competition):
    entry = StageEntryFactory(competition=competition)

    with pytest.raises(DomainError) as error:
        save_arrival_form(entry, needs=[LogisticsNeed.MEAL])

    assert error.value.machine_code == "ONSITE_LOGISTICS_DISABLED"
    assert ArrivalForm.objects.count() == 0


def test_recording_attendance_without_the_flag_is_refused(competition):
    entry = StageEntryFactory(competition=competition)

    with pytest.raises(DomainError):
        record_attendance(entry, present=True)

    assert AttendanceRecord.objects.count() == 0


def test_lists_of_a_stage_without_the_flag_are_empty(competition):
    stage = StageFactory(competition=competition)

    assert arrival_rows(stage) == []
    assert attendance_rows(stage) == []


# --- miejsca zawodów -------------------------------------------------------------------------------


def test_venue_is_created_and_listed(onsite):
    venue = create_venue(onsite, name="Wydział Fizyki UJ", city="Kraków", capacity=120)

    assert list(venues_for(onsite)) == [venue]
    assert str(venue) == "Wydział Fizyki UJ (Kraków)"


def test_venue_name_is_unique_within_the_competition(onsite):
    create_venue(onsite, name="Aula główna")

    with pytest.raises(ValidationError):
        create_venue(onsite, name="Aula główna")


def test_the_same_venue_name_is_free_in_another_competition(onsite, other_competition):
    create_venue(onsite, name="Aula główna")
    enable_logistics(other_competition)

    create_venue(other_competition, name="Aula główna")

    assert Venue.objects.count() == 2


def test_update_venue_changes_only_what_was_given(onsite):
    venue = create_venue(onsite, name="Aula", city="Kraków")

    update_venue(venue, city="Gdańsk")

    venue.refresh_from_db()
    assert (venue.name, venue.city) == ("Aula", "Gdańsk")


def test_venues_of_a_are_invisible_to_b(onsite, other_competition):
    """Reguła izolacji § 5.7 na poziomie querysetu – tańsza i bliższa regule niż przejście widokiem."""
    enable_logistics(other_competition)
    create_venue(onsite, name="Aula A")
    create_venue(other_competition, name="Aula B")

    assert not Venue.objects.for_competition(onsite).filter(competition=other_competition).exists()
    assert [venue.name for venue in venues_for(other_competition)] == ["Aula B"]


# --- deklaracja przyjazdu --------------------------------------------------------------------------


def test_arrival_form_is_saved_with_ordinary_needs(entry, onsite):
    venue = create_venue(onsite, name="Aula")

    form = save_arrival_form(
        entry,
        venue=venue,
        arrives_on=date(2027, 6, 4),
        departs_on=date(2027, 6, 7),
        needs=[LogisticsNeed.MEAL, LogisticsNeed.ACCOMMODATION],
    )

    assert form.needs == [LogisticsNeed.ACCOMMODATION, LogisticsNeed.MEAL]
    assert need_labels(form.needs) == ["nocleg", "wyżywienie"]
    assert form.venue_id == venue.pk


def test_saving_twice_updates_the_same_declaration(entry):
    save_arrival_form(entry, needs=[LogisticsNeed.MEAL])
    save_arrival_form(entry, needs=[LogisticsNeed.TRANSPORT])

    assert ArrivalForm.objects.count() == 1
    assert ArrivalForm.objects.get().needs == [LogisticsNeed.TRANSPORT]


def test_departure_before_arrival_is_refused(entry):
    with pytest.raises(ValidationError):
        save_arrival_form(entry, arrives_on=date(2027, 6, 7), departs_on=date(2027, 6, 4))


def test_unknown_need_is_an_error_not_a_silent_skip():
    with pytest.raises(ValidationError):
        validated_needs(["PARKING"])


def test_a_venue_of_another_competition_does_not_enter_a_declaration(entry, other_competition):
    enable_logistics(other_competition)
    foreign = create_venue(other_competition, name="Cudza aula")

    with pytest.raises(ValidationError):
        save_arrival_form(entry, venue=foreign)


def test_needs_summary_counts_people_not_declarations(onsite):
    stage = StageFactory(competition=onsite)
    for needs in ([LogisticsNeed.MEAL], [LogisticsNeed.MEAL, LogisticsNeed.ACCOMMODATION]):
        save_arrival_form(StageEntryFactory(competition=onsite, stage=stage), needs=needs)

    summary = needs_summary(stage)

    assert summary[LogisticsNeed.MEAL] == 2
    assert summary[LogisticsNeed.ACCOMMODATION] == 1
    assert summary[LogisticsNeed.TRANSPORT] == 0


# --- dane szczególne: decyzja organizatora D21 -------------------------------------------------------


def test_special_needs_are_not_collected_by_default(onsite):
    """Sama flaga obszaru **nie** włącza zbierania danych, które bywają danymi o zdrowiu."""
    assert collects_special_needs(onsite) is False
    assert [value for value, _ in available_needs(onsite)] == [
        LogisticsNeed.ACCOMMODATION,
        LogisticsNeed.MEAL,
        LogisticsNeed.TRANSPORT,
    ]


def test_special_needs_and_note_are_dropped_when_collection_is_off(entry):
    form = save_arrival_form(
        entry,
        needs=[LogisticsNeed.MEAL, LogisticsNeed.DIET, LogisticsNeed.ACCESSIBILITY],
        note="dieta bezglutenowa",
    )

    assert form.needs == [LogisticsNeed.MEAL]
    assert form.note == ""


def test_the_coordinator_row_has_no_note_column_without_collection(entry):
    save_arrival_form(entry, needs=[LogisticsNeed.MEAL], note="cokolwiek")

    row = arrival_rows(entry.stage)[0]

    assert "note" not in row


def test_special_needs_are_collected_after_the_organizer_turns_them_on(entry, onsite):
    set_special_needs_collection(onsite, enabled=True)

    form = save_arrival_form(entry, needs=[LogisticsNeed.DIET], note="dieta bezmięsna")

    assert collects_special_needs(onsite) is True
    assert form.needs == [LogisticsNeed.DIET]
    assert form.note == "dieta bezmięsna"
    assert arrival_rows(entry.stage)[0]["note"] == "dieta bezmięsna"
    assert LogisticsNeed.DIET in [value for value, _ in available_needs(onsite)]


def test_turning_collection_off_hides_the_note_from_the_coordinator_screen(entry, onsite):
    set_special_needs_collection(onsite, enabled=True)
    save_arrival_form(entry, needs=[LogisticsNeed.DIET], note="dieta bezmięsna")

    set_special_needs_collection(onsite, enabled=False)

    assert "note" not in arrival_rows(entry.stage)[0]


def test_the_note_never_enters_the_audit_log(entry, onsite):
    """Audyt czyta szersze grono niż formularz, a wpis zostaje w dzienniku bezterminowo."""
    from apps.core.models import AuditLog

    set_special_needs_collection(onsite, enabled=True)
    save_arrival_form(entry, needs=[LogisticsNeed.DIET], note="dieta bezglutenowa")

    entries = AuditLog.objects.filter(action="arrival_form.saved")
    assert entries.count() == 1
    assert "bezglutenowa" not in str(entries.get().diff)
    assert entries.get().diff["has_note"] is True


def test_collection_settings_are_scoped_to_one_competition(onsite, other_competition):
    enable_logistics(other_competition)
    set_special_needs_collection(onsite, enabled=True)

    assert collects_special_needs(other_competition) is False
    assert LogisticsSettings.objects.filter(competition=onsite).count() == 1


def test_switching_collection_is_refused_without_the_flag(competition):
    with pytest.raises(DomainError):
        set_special_needs_collection(competition, enabled=True)


# --- rejestr czynności przetwarzania (art. 30 RODO) ---------------------------------------------------


def test_the_register_of_competition_one_is_unchanged(competition):
    """Konkurs #1 nie zbiera danych logistycznych, więc jego rejestr nie rośnie o ani jeden wiersz."""
    assert activities_for(competition) == ACTIVITIES
    assert len(as_rows()) == len(ACTIVITIES)


def test_the_register_grows_only_where_special_needs_are_collected(onsite):
    assert activities_for(onsite) == ACTIVITIES

    set_special_needs_collection(onsite, enabled=True)
    activities = activities_for(onsite)

    assert len(activities) == len(ACTIVITIES) + 1
    entry = activities[-1]
    assert entry.key == "logistyka"
    assert "art. 9" in entry.legal_basis
    assert len(as_rows(activities)) == len(ACTIVITIES) + 1


# --- obecność ---------------------------------------------------------------------------------------


def test_attendance_is_recorded_with_the_time_it_happened(entry):
    record = record_attendance(entry, present=True)

    assert record.present is True
    assert record.checked_in_at is not None


def test_marking_absence_clears_the_time(entry):
    record_attendance(entry, present=True)
    record = record_attendance(entry, present=False)

    assert AttendanceRecord.objects.count() == 1
    assert record.checked_in_at is None


def test_attendance_rows_list_everyone_entitled_to_enter(onsite):
    """Lista obecności jest dokumentem wnoszonym na salę: ma na niej być każdy zapisany do etapu."""
    stage = StageFactory(competition=onsite)
    present = StageEntryFactory(competition=onsite, stage=stage)
    StageEntryFactory(competition=onsite, stage=stage)
    record_attendance(present, present=True)

    rows = attendance_rows(stage)

    assert len(rows) == 2
    assert sum(1 for row in rows if row["present"]) == 1


def test_attendance_of_another_competition_is_not_visible(onsite, other_competition):
    enable_logistics(other_competition)
    record_attendance(StageEntryFactory(competition=onsite), present=True)
    record_attendance(StageEntryFactory(competition=other_competition), present=True)

    assert AttendanceRecord.objects.for_competition(onsite).count() == 1
    assert ArrivalForm.objects.for_competition(other_competition).count() == 0
