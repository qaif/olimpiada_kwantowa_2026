"""Listy do druku etapu stacjonarnego: co jest w wierszach i czego w nich nigdy nie ma.

Wiersze sprawdzamy osobno od składu PDF-a i to jest cały zamysł podziału w
``apps.integrations.exports``: o zawartości dokumentu rozstrzyga :func:`logistics_list_rows`,
a ``render_logistics_list`` wyłącznie ją składa. Test czytający bajty PDF-a odpowiadałby na pytanie
„czy reportlab działa”, a pytanie brzmi „czy uwaga o diecie wyszła na listę obecności”.

Reguła D21 (``docs/UNIWERSALNY-ETAP-2.md`` § 6) ma tu dwa dowody: bez zgody organizatora kolumny
„Uwagi” nie ma nigdzie, a z jego zgodą jest **wyłącznie** na liście żywieniowej – bo tylko tam ma
adresata. Lista obecności krąży po sali i nie niesie niczego, co bywa daną o zdrowiu.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.competitions.logistics import (
    LogisticsNeed,
    create_venue,
    record_attendance,
    save_arrival_form,
    set_special_needs_collection,
)
from apps.competitions.tests.factories import StageEntryFactory
from apps.integrations.exports import (
    LIST_ACCOMMODATION,
    LIST_ATTENDANCE,
    LIST_MEAL,
    logistics_list_filename,
    logistics_list_rows,
    render_logistics_list,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def onsite(competition):
    """Konkurs #1 z włączoną logistyką – bez niej wszystkie trzy listy są z definicji puste."""
    competition.feature_flags = {**(competition.feature_flags or {}), "onsite_logistics": True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def stage_with_arrivals(onsite, stage):
    """Etap z dwiema deklaracjami: nocleg plus wyżywienie oraz sam dojazd."""
    venue = create_venue(onsite, name="Aula główna", city="Kraków")
    sleeper = StageEntryFactory(competition=onsite, stage=stage)
    commuter = StageEntryFactory(competition=onsite, stage=stage)
    save_arrival_form(
        sleeper,
        venue=venue,
        arrives_on=date(2027, 6, 4),
        departs_on=date(2027, 6, 7),
        needs=[LogisticsNeed.ACCOMMODATION, LogisticsNeed.MEAL],
    )
    save_arrival_form(commuter, venue=venue, needs=[LogisticsNeed.TRANSPORT])
    return stage


def test_attendance_list_has_everyone_and_a_column_for_a_signature(onsite, stage):
    present = StageEntryFactory(competition=onsite, stage=stage)
    StageEntryFactory(competition=onsite, stage=stage)
    record_attendance(present, present=True)

    header, rows = logistics_list_rows(stage, LIST_ATTENDANCE)

    assert header[-1] == "Podpis"
    assert len(rows) == 2
    assert [row[-1] for row in rows] == ["", ""]
    assert sum(1 for row in rows if row[-2] == "obecny") == 1


def test_accommodation_list_holds_only_people_who_asked_for_a_bed(stage_with_arrivals):
    header, rows = logistics_list_rows(stage_with_arrivals, LIST_ACCOMMODATION)

    assert len(rows) == 1
    assert rows[0][-2:] == ["2027-06-04", "2027-06-07"]
    assert "Uwagi" not in header


def test_meal_list_holds_only_people_who_asked_for_food(stage_with_arrivals):
    _, rows = logistics_list_rows(stage_with_arrivals, LIST_MEAL)

    assert len(rows) == 1
    assert rows[0][-1] == "nocleg, wyżywienie"


def test_no_list_has_a_note_column_without_the_organizers_decision(stage_with_arrivals):
    for kind in (LIST_ATTENDANCE, LIST_ACCOMMODATION, LIST_MEAL):
        header, _ = logistics_list_rows(stage_with_arrivals, kind)
        assert "Uwagi" not in header, kind


def test_the_note_reaches_the_kitchen_and_only_the_kitchen(onsite, stage):
    """Z decyzją organizatora uwaga jest na liście żywieniowej – i nigdzie indziej."""
    set_special_needs_collection(onsite, enabled=True)
    entry = StageEntryFactory(competition=onsite, stage=stage)
    save_arrival_form(entry, needs=[LogisticsNeed.DIET], note="dieta bezmięsna")
    record_attendance(entry, present=True)

    meal_header, meal_rows = logistics_list_rows(stage, LIST_MEAL)
    attendance_header, attendance_rows_ = logistics_list_rows(stage, LIST_ATTENDANCE)

    assert meal_header[-1] == "Uwagi"
    assert meal_rows[0][-1] == "dieta bezmięsna"
    assert "Uwagi" not in attendance_header
    assert all("bezmięsna" not in cell for cell in attendance_rows_[0])


def test_lists_of_a_competition_without_the_flag_are_empty(stage):
    for kind in (LIST_ATTENDANCE, LIST_ACCOMMODATION, LIST_MEAL):
        _, rows = logistics_list_rows(stage, kind)
        assert rows == [], kind


def test_unknown_kind_of_list_is_an_error(stage):
    with pytest.raises(ValueError, match="Nieznany rodzaj listy"):
        logistics_list_rows(stage, "grafik")


def test_every_list_renders_as_a_pdf(stage_with_arrivals):
    for kind in (LIST_ATTENDANCE, LIST_ACCOMMODATION, LIST_MEAL):
        pdf = render_logistics_list(stage_with_arrivals, kind)

        assert pdf.startswith(b"%PDF"), kind
        assert len(pdf) > 1000, kind


def test_filename_carries_no_personal_data(stage_with_arrivals):
    name = logistics_list_filename(stage_with_arrivals, LIST_ATTENDANCE)

    assert name.startswith(f"lista-{LIST_ATTENDANCE}-etap-{stage_with_arrivals.pk}-")
    assert name.endswith(".pdf")
