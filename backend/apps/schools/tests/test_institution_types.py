"""Rodzaj placówki w słowniku: reguły doboru z wykazu, wgrywanie i **zawężone** wygaszanie.

Plik pilnuje jednej rzeczy, której nie pilnował nikt przed etapem 2: słownik przestał być listą
samych szkół ponadpodstawowych, a komenda ``seed_schools`` została **jedna**. Wgranie wykazu
jednego rodzaju placówki nie ma prawa dotknąć wierszy innego rodzaju – inaczej pierwszy konkurs
z uczelniami zgasiłby osiem tysięcy szkół Olimpiady Kwantowej przy najbliższym wdrożeniu.

Testy reguły doboru korzystają z arkusza budowanego w ``test_sio.py`` (ten sam nagłówek i ten sam
wiersz wzorcowy), bo pytanie jest tu inne – nie „jak czytamy wykaz”, tylko „co odróżnia dwie
reguły doboru z tego samego arkusza”.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.schools.models import InstitutionType, School, SchoolKind
from apps.schools.sio import (
    DEFAULT_INSTITUTION_TYPE,
    INSTITUTION_KINDS,
    PRIMARY_KINDS,
    SECONDARY_KINDS,
    SioFormatError,
    read_schools,
    school_from_row,
)

from .factories import SchoolFactory
from .test_seed_schools import entry, fixture_file
from .test_sio import row, workbook_with


def primary_entry(**overrides) -> dict:
    """Wiersz fixture'u szkoły podstawowej – taki, jaki pisze ``sio.school_from_row``."""
    return entry(
        rspo=7000,
        name="SZKOŁA PODSTAWOWA NR 1 W ŁODZI",
        kind=SchoolKind.INNA,
        institution_type=InstitutionType.PRIMARY,
        **overrides,
    )


# --- reguła doboru wierszy z wykazu ---------------------------------------------------------


def test_the_two_selection_rules_do_not_overlap():
    """Jeden „Typ podmiotu” nie może być jednocześnie podstawowy i ponadpodstawowy.

    Gdyby był, ten sam numer RSPO trafiłby do dwóch plików z dwiema wartościami
    ``institution_type`` – a wtedy dwa kolejne wdrożenia przerzucałyby wiersz tam i z powrotem.
    """
    assert set(SECONDARY_KINDS) & set(PRIMARY_KINDS) == set()


def test_selection_rules_speak_the_vocabulary_of_the_models():
    """``sio`` nie importuje Django, więc zgodność napisów z ``choices`` pilnuje test."""
    assert set(INSTITUTION_KINDS) <= set(InstitutionType.values)
    assert set(INSTITUTION_KINDS[DEFAULT_INSTITUTION_TYPE]) == set(SECONDARY_KINDS)
    for kinds in INSTITUTION_KINDS.values():
        assert set(kinds.values()) <= set(SchoolKind.values)


def test_secondary_rule_is_the_default_and_marks_the_rows():
    school = school_from_row(row())

    assert school["institution_type"] == InstitutionType.SECONDARY


def test_primary_rule_takes_the_rows_that_the_secondary_rule_rejects():
    primary = row(**{"Typ podmiotu": "Szkoła podstawowa"})

    assert school_from_row(primary) is None
    selected = school_from_row(primary, InstitutionType.PRIMARY)
    assert selected["institution_type"] == InstitutionType.PRIMARY
    # ``SchoolKind`` zostaje bez zmian (T21 nie wolno go ruszać) – szkoła podstawowa nie jest ani
    # liceum, ani technikum, więc idzie na „inna” i staje za nimi w porządku podpowiedzi.
    assert selected["kind"] == SchoolKind.INNA


def test_primary_rule_rejects_secondary_schools():
    assert school_from_row(row(), InstitutionType.PRIMARY) is None


def test_primary_rule_still_rejects_schools_for_adults():
    assert (
        school_from_row(
            row(**{"Typ podmiotu": "Szkoła podstawowa", "Kategoria uczniów": "Dorośli"}),
            InstitutionType.PRIMARY,
        )
        is None
    )


def test_reading_a_workbook_with_the_primary_rule(tmp_path):
    path = workbook_with(
        [
            row(),
            row(**{"RSPO": 111, "Typ podmiotu": "Szkoła podstawowa"}),
            row(**{"RSPO": 222, "Typ podmiotu": "Przedszkole"}),
        ],
        tmp_path,
    )

    schools = list(read_schools(path, institution_type=InstitutionType.PRIMARY))

    assert [school["rspo"] for school in schools] == [111]


def test_unknown_institution_type_stops_the_reader(tmp_path):
    """Rodzaj bez reguły doboru (uczelnie, placówka poza Polską) nie powstaje z arkusza SIO."""
    path = workbook_with([row()], tmp_path)

    with pytest.raises(SioFormatError, match="UNIVERSITY"):
        list(read_schools(path, institution_type=InstitutionType.UNIVERSITY))


# --- wgrywanie i wygaszanie -----------------------------------------------------------------


@pytest.mark.django_db
def test_row_without_the_field_is_a_secondary_school(tmp_path):
    """Plik z repozytorium nie ma tego pola i **nie dostaje go** – ma znaczyć dokładnie to, co dziś."""
    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))

    assert School.objects.get(rspo=1000).institution_type == InstitutionType.SECONDARY


@pytest.mark.django_db
def test_unknown_institution_type_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="rodzaj placówki"):
        call_command("seed_schools", fixture=fixture_file(tmp_path, [entry(institution_type="PRZEDSZKOLE")]))
    assert School.objects.count() == 0


@pytest.mark.django_db
def test_seed_schools_does_not_deactivate_other_institution_types(tmp_path):
    """Wgranie jednego wykazu nie rusza wierszy pochodzących z innego wykazu.

    Test wymagany przez plan (§ 1.3.2): zawężenie wygaszania jest **jedyną** zmianą zachowania
    ``seed_schools`` w etapie 2, a jest to kod, który chodzi przy każdym wdrożeniu Konkursu #1.
    """
    secondary = SchoolFactory(rspo=6001)
    university = SchoolFactory(rspo=6002, institution_type=InstitutionType.UNIVERSITY)
    stale_primary = SchoolFactory(rspo=6003, institution_type=InstitutionType.PRIMARY)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [primary_entry()]))

    for school in (secondary, university, stale_primary):
        school.refresh_from_db()
    assert (secondary.is_active, university.is_active) == (True, True)
    # W obrębie własnego rodzaju reguła zostaje bez zmiany: nieobecny wiersz jest wygaszany…
    assert stale_primary.is_active is False
    # …i nadal **nie jest kasowany**.
    assert School.objects.filter(rspo=6003).exists()
    assert School.objects.get(rspo=7000).institution_type == InstitutionType.PRIMARY


@pytest.mark.django_db
def test_seeding_the_secondary_list_leaves_the_other_lists_alone(tmp_path):
    """Kierunek odwrotny – ten, który chodzi na produkcji Konkursu #1 przy każdym wdrożeniu."""
    university = SchoolFactory(rspo=6102, institution_type=InstitutionType.UNIVERSITY)
    primary = SchoolFactory(rspo=6103, institution_type=InstitutionType.PRIMARY)
    stale_secondary = SchoolFactory(rspo=6104)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))

    for school in (university, primary, stale_secondary):
        school.refresh_from_db()
    assert (university.is_active, primary.is_active) == (True, True)
    assert stale_secondary.is_active is False


@pytest.mark.django_db
def test_a_file_with_two_kinds_of_lists_deactivates_within_both(tmp_path):
    """Zawężenie idzie po **zbiorze** rodzajów w pliku, a nie po pierwszym napotkanym wierszu."""
    stale_secondary = SchoolFactory(rspo=6201)
    stale_primary = SchoolFactory(rspo=6202, institution_type=InstitutionType.PRIMARY)
    university = SchoolFactory(rspo=6203, institution_type=InstitutionType.UNIVERSITY)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry(), primary_entry()]))

    for school in (stale_secondary, stale_primary, university):
        school.refresh_from_db()
    assert (stale_secondary.is_active, stale_primary.is_active) == (False, False)
    assert university.is_active is True


@pytest.mark.django_db
def test_reclassified_row_follows_the_new_file(tmp_path):
    """Upsert po RSPO obejmuje także rodzaj placówki – inaczej wiersz zostałby w starym wykazie."""
    SchoolFactory(rspo=7000)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [primary_entry()]))

    assert School.objects.get(rspo=7000).institution_type == InstitutionType.PRIMARY


@pytest.mark.django_db
def test_bundled_fixture_is_entirely_secondary_and_spares_the_rest():
    """Osiem tysięcy wierszy z repozytorium to szkoły ponadpodstawowe – i tylko one są wygaszane."""
    university = SchoolFactory(rspo=9001, institution_type=InstitutionType.UNIVERSITY)

    call_command("seed_schools")

    assert School.objects.exclude(institution_type=InstitutionType.SECONDARY).count() == 1
    assert School.objects.filter(institution_type=InstitutionType.SECONDARY).count() > 8000
    university.refresh_from_db()
    assert university.is_active is True
