"""Blok „terminy etapów” (``stage_timeline``) na stronie ``/harmonogram/``.

Cała rzecz sprowadza się do jednego zdania: **strona informacyjna i serwer ogłaszają ten sam
termin**. Dopóki terminarz był tabelą wpisaną w treść, były to dwa niezależne źródła i rozjazd
między nimi był kwestią czasu — a kosztuje uczestnika pracę oddaną „na czas” według strony i po
terminie według systemu. Testy sprawdzają więc drogę od panelu koordynatora do strony publicznej.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.cms.models import ContentPage
from apps.cms.timeline import stage_rows
from apps.competitions.models import Stage, StageFormat, StageKind
from apps.competitions.tests.factories import StageFactory
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

WARSAW_FORMAT = "%Y-%m-%dT%H:%M"


@pytest.fixture
def harmonogram():
    call_command("seed_legacy_content", verbosity=0)
    return ContentPage.objects.get(slug="harmonogram")


def stage_form_data(stage: Stage, **overrides) -> dict:
    data = {
        "name": stage.name,
        "format": stage.format,
        "location": stage.location,
        "grace_seconds": stage.grace_seconds,
        "opens_at": timezone.localtime(stage.opens_at).strftime(WARSAW_FORMAT),
        "deadline_at": timezone.localtime(stage.deadline_at).strftime(WARSAW_FORMAT),
        "review_deadline_at": timezone.localtime(stage.review_deadline_at).strftime(WARSAW_FORMAT),
        "appeal_window_opens_at": timezone.localtime(stage.appeal_window_opens_at).strftime(WARSAW_FORMAT),
        "appeal_window_closes_at": timezone.localtime(stage.appeal_window_closes_at).strftime(WARSAW_FORMAT),
    }
    data.update(overrides)
    return data


# --- treść strony -------------------------------------------------------------------------------


def test_marker_in_the_source_file_becomes_a_block(harmonogram):
    """``{{stage_timeline}}`` w pliku źródłowym → blok StreamFielda, a nie akapit z klamrami."""
    kinds = [block.block_type for block in harmonogram.body]

    assert kinds.count("stage_timeline") == 1
    assert "{{stage_timeline}}" not in str(harmonogram.body)


def test_empty_state_when_there_is_no_current_edition(web_client, harmonogram):
    content = web_client.get("/harmonogram/").content.decode()

    assert "Terminy zostaną ogłoszone" in content
    # Tabela warsztatów zostaje – to treść redakcyjna, której system zawodów nie zna.
    assert "Harmonogram warsztatów" in content
    assert "Liczby zespolone" in content


def test_timeline_shows_every_stage_of_the_current_edition(web_client, harmonogram, edition, open_stage):
    content = web_client.get("/harmonogram/").content.decode()

    assert "Eliminacje" in content
    assert "<dt>Oddanie rozwiązań</dt>" in content
    assert "<dt>Reklamacje</dt>" in content
    assert "Terminy zostaną ogłoszone" not in content


# --- droga z panelu koordynatora ------------------------------------------------------------------


def test_change_in_the_panel_is_visible_on_the_page(web_client, harmonogram, coordinator, edition):
    """Koordynator przesuwa deadline i wskazuje Kraków – strona pokazuje to od razu, bez seeda."""
    stage = StageFactory(edition=edition, kind=StageKind.FINAL)
    new_deadline = timezone.localtime(stage.deadline_at) + timedelta(days=9)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{stage.pk}/edit/",
        stage_form_data(stage, deadline_at=new_deadline.strftime(WARSAW_FORMAT), location="Kraków"),
    )
    assert response.status_code == 302

    web_client.logout()
    content = web_client.get("/harmonogram/").content.decode()

    assert "Kraków" in content
    assert f"{new_deadline.day} " in content
    assert new_deadline.strftime("%H:%M") in content
    # Ta sama zmiana stoi na osi czasu strony głównej – oba ekrany czytają jedno źródło.
    assert "Kraków" in web_client.get("/").content.decode()


def test_stage_added_in_the_panel_appears_on_the_page(web_client, harmonogram, coordinator, edition):
    web_client.force_login(coordinator)
    opens = timezone.localtime(timezone.now()) + timedelta(days=5)

    web_client.post(
        "/coordinator/stages/new/",
        {
            "kind": StageKind.ELIM,
            "name": "",
            "format": StageFormat.SUBMISSIONS,
            "location": "",
            "grace_seconds": 0,
            "opens_at": opens.strftime(WARSAW_FORMAT),
            "deadline_at": (opens + timedelta(days=10)).strftime(WARSAW_FORMAT),
            "review_deadline_at": (opens + timedelta(days=24)).strftime(WARSAW_FORMAT),
            "appeal_window_opens_at": (opens + timedelta(days=26)).strftime(WARSAW_FORMAT),
            "appeal_window_closes_at": (opens + timedelta(days=33)).strftime(WARSAW_FORMAT),
        },
    )
    web_client.logout()

    content = web_client.get("/harmonogram/").content.decode()
    assert "Eliminacje" in content
    assert "nadchodzący" in content


def test_custom_stage_name_and_interview_format_are_visible_on_the_page(web_client, harmonogram, edition):
    """Nazwa nadana przez koordynatora zastępuje etykietę rodzaju, a rozmowa – „oddanie rozwiązań”.

    Etap w formie rozmowy nie ma czego oddawać, więc rubryka „Oddanie rozwiązań” opisywałaby
    czynność, której na tym etapie nie ma; jej miejsce zajmuje koniec okna rozmów.
    """
    StageFactory(
        edition=edition,
        kind=StageKind.DISTRICT,
        name="Etap II – rozmowy kwalifikacyjne",
        format=StageFormat.INTERVIEW,
    )

    content = web_client.get("/harmonogram/").content.decode()

    assert "Etap II – rozmowy kwalifikacyjne" in content
    assert "Wojewódzki" not in content
    assert "<dt>Rozmowy do</dt>" in content
    assert "rozmowa kwalifikacyjna online" in content
    # Oś czasu strony głównej czyta to samo źródło, więc pokazuje to samo.
    assert "Etap II – rozmowy kwalifikacyjne" in web_client.get("/").content.decode()


# --- stany etapu --------------------------------------------------------------------------------


def test_status_reflects_the_clock_and_the_publication(edition, open_stage):
    upcoming = StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        opens_at=timezone.now() + timedelta(days=10),
    )
    rows = {row["stage"].pk: row for row in stage_rows(edition)}

    assert rows[open_stage.pk]["status"] == "open"
    assert rows[upcoming.pk]["status"] == "upcoming"

    ResultsPublication.objects.create(stage=open_stage, anonymization=Anonymization.CODE, snapshot=[])
    assert stage_rows(edition)[0]["status"] == "published"


def test_stage_rows_without_a_current_edition_are_empty():
    assert stage_rows() == []


def test_timeline_costs_two_queries_regardless_of_the_number_of_stages(
    django_assert_max_num_queries, edition, open_stage
):
    """Jedno zapytanie o etapy, jedno o publikacje – lista rośnie o wiersze, nie o zapytania."""
    StageFactory(edition=edition, kind=StageKind.DISTRICT)
    StageFactory(edition=edition, kind=StageKind.FINAL)

    with django_assert_max_num_queries(2):
        rows = stage_rows(edition)

    assert len(rows) == 3
