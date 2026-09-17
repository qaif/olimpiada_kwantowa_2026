"""Eksporty na zewnątrz: lista dla kuratorium, protokół etapu i zrzut edycji."""

import json

import pytest

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageEntryStatus
from apps.integrations.exports import (
    KURATORIUM_HEADER,
    edition_export,
    kuratorium_dataset,
    render_stage_protocol,
    voivodeship_label,
)
from apps.results.models import Anonymization, ResultsPublication
from apps.results.tests.conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

MAZOWSZE = "mazowieckie"
POMORZE = "pomorskie"


@pytest.fixture
def scored_stage(edition):
    """Etap z dwoma uczestnikami z różnych województw i różnymi wynikami."""
    stage = make_stage(edition=edition, min_points=6)
    graded_entry(
        stage,
        [6, 5],
        participant=ParticipantFactory(
            user__first_name="Łucja",
            user__last_name="Śniadecka",
            school="XIV LO Warszawa",
            district=MAZOWSZE,
            grade=3,
        ),
    )
    graded_entry(
        stage,
        [0, 2],
        participant=ParticipantFactory(
            user__first_name="Jan",
            user__last_name="Gdański",
            school="I LO Gdańsk",
            district=POMORZE,
            grade=2,
        ),
    )
    return stage


def test_voivodeship_label_translates_slug():
    assert voivodeship_label("lodzkie") == "łódzkie"
    assert voivodeship_label("nieistniejace") == "nieistniejace"


def test_kuratorium_dataset_is_limited_to_one_voivodeship(scored_stage):
    dataset = kuratorium_dataset(scored_stage, MAZOWSZE)
    rows = list(dataset.rows)

    assert dataset.header == KURATORIUM_HEADER
    assert dataset.count == 1
    assert len(rows) == 1
    code, first_name, last_name, school, city, grade, total, qualification = rows[0]
    assert (first_name, last_name) == ("Łucja", "Śniadecka")
    assert school == "XIV LO Warszawa"
    assert grade == 3
    assert total == 11
    assert qualification == "zakwalifikowany"
    # Szkoła wpisana ręcznie nie ma miejscowości jako osobnej danej – i nie zgadujemy jej.
    assert city == ""
    assert code


def test_kuratorium_dataset_marks_who_did_not_qualify(scored_stage):
    rows = list(kuratorium_dataset(scored_stage, POMORZE).rows)
    assert rows[0][-1] == "niezakwalifikowany"


def test_kuratorium_dataset_marks_disqualified(scored_stage):
    entry = scored_stage.entries.get(participant__district=MAZOWSZE)
    entry.status = StageEntryStatus.DISQUALIFIED
    entry.save(update_fields=["status"])

    rows = list(kuratorium_dataset(scored_stage, MAZOWSZE).rows)
    assert rows[0][-1] == "zdyskwalifikowany"


def test_protocol_is_a_pdf_with_signature_block(scored_stage):
    pdf = render_stage_protocol(scored_stage)

    assert pdf.startswith(b"%PDF")
    # Dokument ma mieć więcej niż samą tabelę – blok podpisów jest częścią protokołu.
    assert len(pdf) > 2000


def test_edition_export_has_structure_without_personal_data(scored_stage, edition):
    ResultsPublication.objects.create(
        stage=scored_stage,
        anonymization=Anonymization.CODE,
        snapshot=[{"rank": 1, "display": "OLM-0001", "points": {"1": 6}, "total": 6, "qualified": True}],
    )

    payload = edition_export(edition)
    text = json.dumps(payload, ensure_ascii=False)

    assert payload["format"] == "olimpiada.edition.v1"
    assert payload["edition"]["year_label"] == edition.year_label
    stage_export = next(item for item in payload["stages"] if item["id"] == scored_stage.pk)
    assert len(stage_export["problems"]) == 2
    assert len(stage_export["entries"]) == 2
    assert stage_export["results"]["anonymization"] == Anonymization.CODE
    # Wpisy uczestników idą pod kodami publicznymi – imion, nazwisk i adresów tu nie ma.
    assert "Śniadecka" not in text
    assert "Gdański" not in text
    expected = {"public_code", "voivodeship", "grade", "status", "total_points"}
    assert all(set(entry) == expected for entry in stage_export["entries"])


def test_edition_export_handles_stage_without_results(stage, edition):
    payload = edition_export(edition)
    assert payload["stages"][0]["results"] is None
