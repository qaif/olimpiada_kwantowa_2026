"""``manage.py seed_schools``: upsert po RSPO, wygaszanie nieobecnych, twarda walidacja pliku."""

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.schools.models import School

from .factories import SchoolFactory


def fixture_file(tmp_path, rows) -> str:
    path = tmp_path / "szkoly.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(path)


def entry(**overrides) -> dict:
    row = {
        "rspo": 1000,
        "name": "III LICEUM OGÓLNOKSZTAŁCĄCE W ŁODZI",
        "kind": "LO",
        "voivodeship": "lodzkie",
        "city": "Łódź",
        "postal_code": "90-001",
        "address": "ul. Piotrkowska 1",
        "is_public": True,
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
def test_seed_creates_rows_and_fills_the_search_column(tmp_path):
    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))

    school = School.objects.get(rspo=1000)
    assert school.city == "Łódź"
    # Obie kolumny porównawcze powstają mimo ``bulk_create`` (które omija ``Model.save()``).
    assert school.search_text == "iii liceum ogolnoksztalcace w lodzi lodz"
    # Osobna kolumna samej miejscowości – po niej chodzi krok „Miejscowość” w wyszukiwarce.
    assert school.city_search == "lodz"
    assert school.source_year == "2025/2026"


@pytest.mark.django_db
def test_seed_is_idempotent_and_updates_changed_rows(tmp_path):
    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))
    call_command(
        "seed_schools",
        fixture=fixture_file(tmp_path, [entry(name="III LO IM. NOWEGO PATRONA", city="Zgierz")]),
    )

    assert School.objects.count() == 1
    school = School.objects.get(rspo=1000)
    assert school.name == "III LO IM. NOWEGO PATRONA"
    assert school.search_text == "iii lo im. nowego patrona zgierz"
    # Przeprowadzka szkoły przestawia także kolumnę miejscowości – inaczej zostałaby w Łodzi.
    assert school.city_search == "zgierz"


@pytest.mark.django_db
def test_school_missing_from_the_new_list_is_deactivated_not_deleted(tmp_path):
    stale = SchoolFactory(rspo=4242)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))

    stale.refresh_from_db()
    assert stale.is_active is False
    assert School.objects.filter(rspo=4242).exists()


@pytest.mark.django_db
def test_school_that_returns_to_the_list_becomes_active_again(tmp_path):
    SchoolFactory(rspo=1000, is_active=False)

    call_command("seed_schools", fixture=fixture_file(tmp_path, [entry()]))

    assert School.objects.get(rspo=1000).is_active is True


@pytest.mark.django_db
def test_unknown_voivodeship_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="województwo"):
        call_command("seed_schools", fixture=fixture_file(tmp_path, [entry(voivodeship="mazowsze")]))
    assert School.objects.count() == 0


@pytest.mark.django_db
def test_unknown_kind_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="typ szkoły"):
        call_command("seed_schools", fixture=fixture_file(tmp_path, [entry(kind="PRZEDSZKOLE")]))


@pytest.mark.django_db
def test_missing_file_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="Nie ma pliku"):
        call_command("seed_schools", fixture=str(tmp_path / "nie-ma.json"))


@pytest.mark.django_db
def test_bundled_fixture_loads_end_to_end():
    """Plik z repozytorium musi dać się wgrać – to on jedzie na produkcję przy każdym wdrożeniu."""
    call_command("seed_schools")

    assert School.objects.filter(is_active=True).count() > 8000
    # Losowa próbka: wykaz zawiera wszystkie 16 województw i wyłącznie znane typy szkół.
    assert School.objects.values("voivodeship").distinct().count() == 16
