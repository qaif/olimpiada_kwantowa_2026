"""Blok „terminy etapów” (``stage_timeline``) na stronie ``/harmonogram/``.

Cała rzecz sprowadza się do jednego zdania: **strona informacyjna i serwer ogłaszają ten sam
termin**. Dopóki terminarz był tabelą wpisaną w treść, były to dwa niezależne źródła i rozjazd
między nimi był kwestią czasu — a kosztuje uczestnika pracę oddaną „na czas” według strony i po
terminie według systemu. Testy sprawdzają więc drogę od panelu koordynatora do strony publicznej.
"""

from datetime import date, datetime, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.cms.models import ContentPage
from apps.cms.timeline import format_date_range, is_onsite_event, stage_date_range, stage_rows
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
    # Treść redakcyjna strony zostaje – pusty stan dotyczy samego bloku terminów, nie całej strony.
    assert "Zapisy do eliminacji są otwarte" in content
    assert 'href="/warsztaty/"' in content


def test_timeline_shows_every_stage_of_the_current_edition(web_client, harmonogram, edition, open_stage):
    content = web_client.get("/harmonogram/").content.decode()

    assert "Eliminacje" in content
    assert "<dt>Oddanie rozwiązań</dt>" in content
    assert "<dt>Reklamacje</dt>" in content
    assert "Terminy zostaną ogłoszone" not in content


def test_review_deadline_is_labelled_as_the_results_date(web_client, harmonogram, edition, open_stage):
    """„Wyniki do”, nie „Recenzje do”: czytelnikiem tej strony jest uczestnik, nie recenzent.

    To ta sama data (``review_deadline_at``). Uczestnik nie ma nic z recenzowania – interesuje go
    najpóźniejszy moment, w którym dowie się wyniku, i tak ma być podpisany ten wiersz.
    """
    content = web_client.get("/harmonogram/").content.decode()

    assert "<dt>Wyniki do</dt>" in content
    assert "Recenzje do" not in content


# --- etap stacjonarny: jeden termin, nie dwie godziny ---------------------------------------------


def test_onsite_stage_shows_one_date_range_instead_of_opening_and_deadline(web_client, harmonogram, edition):
    """Finał trwa cztery dni w jednym miejscu – ma termin, nie „otwarcie” i „oddanie pliku”.

    Dwa wiersze z godzinami sugerowały tam okno na wysyłkę rozwiązania, którego na miejscu nie ma.
    Zakres wyświetla się w obu miejscach: w bloku na ``/harmonogram/`` i na stronie głównej.
    """
    warsaw = timezone.get_current_timezone()
    StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        location="Kraków",
        opens_at=datetime(2027, 6, 4, 9, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 6, 7, 18, 0, tzinfo=warsaw),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    for path in ("/harmonogram/", "/"):
        content = web_client.get(path).content.decode()
        assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in content, path
        assert "<dt>Miejsce</dt><dd>Kraków</dd>" in content, path
        assert "Otwarcie" not in content, path


def test_location_text_alone_never_hides_the_submission_window(web_client, harmonogram, edition):
    """Regresja z produkcji: „Tryb zdalny” w polu miejsca przy etapie na trzy miesiące.

    Sam wpis w polu miejsca (i okno na różne dni) nie czyni etapu zjazdem – bez wpisanych dni
    wydarzenia strona ma pokazywać otwarcie i godzinę oddania prac, bo to je egzekwuje serwer.
    """
    warsaw = timezone.get_current_timezone()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.ELIM,
        location="Tryb zdalny",
        opens_at=datetime(2026, 11, 15, 12, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 2, 28, 23, 59, tzinfo=warsaw),
    )

    assert is_onsite_event(stage) is False
    content = web_client.get("/harmonogram/").content.decode()
    assert "<dt>Otwarcie</dt>" in content
    assert "28 lutego 2027, 23:59" in content
    assert "<dt>Termin</dt>" not in content


def test_remote_stage_keeps_its_opening_and_deadline(web_client, harmonogram, edition, open_stage):
    """Etap zdalny nie ma miejsca, więc zostaje przy dwóch terminach – zakres go nie dotyczy."""
    content = web_client.get("/harmonogram/").content.decode()

    assert "<dt>Otwarcie</dt>" in content
    assert "<dt>Termin</dt>" not in content


def test_event_dates_win_over_the_submission_window(web_client, harmonogram, edition):
    """Finał trwa 4–7 czerwca, a prace przyjmuje się kilka godzin 5 czerwca – strona ogłasza zjazd.

    To jedyny przypadek, w którym oba fakty są w bazie naraz i się różnią: ``event_range`` jest
    terminem z pisma organizatora, a ``opens_at``/``deadline_at`` sesją, którą egzekwuje serwer.
    Bez pierwszeństwa strona zapraszałaby na jeden dzień z czterech.
    """
    warsaw = timezone.get_current_timezone()
    StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        location="Kraków",
        opens_at=datetime(2027, 6, 5, 9, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 6, 5, 14, 0, tzinfo=warsaw),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    for path in ("/harmonogram/", "/"):
        content = web_client.get(path).content.decode()
        assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in content, path
        # Godziny sesji nie stają na osi czasu jako termin etapu: rubryk „Otwarcie” i „Oddanie
        # rozwiązań” tam nie ma (najbliższy termin uploadu strona główna zapowiada osobno).
        assert "<dt>Otwarcie</dt>" not in content, path
        assert "<dt>Oddanie rozwiązań</dt>" not in content, path


def test_event_dates_make_a_one_day_window_an_event(edition):
    """Sesja w jednym dniu, a zjazd kilkudniowy: to wpisane dni decydują, że etap jest wydarzeniem.

    Dotychczasowa reguła (miejsce + różne dni okna) nie wystarczy – okno mieści się w jednym dniu.
    """
    warsaw = timezone.get_current_timezone()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        location="Kraków",
        opens_at=datetime(2027, 6, 5, 9, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 6, 5, 14, 0, tzinfo=warsaw),
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    assert is_onsite_event(stage) is True
    assert stage_date_range(stage) == "4–7 czerwca 2027"
    assert stage_rows(edition)[0]["date_range"] == "4–7 czerwca 2027"


def test_event_dates_do_not_need_a_location(edition):
    """Wpisane dni wystarczą: zjazd bez wskazanego miejsca (jeszcze nieznanego) też jest zjazdem."""
    stage = StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        location="",
        event_starts_on=date(2027, 6, 4),
        event_ends_on=date(2027, 6, 7),
    )

    assert is_onsite_event(stage) is True


def test_seed_gives_the_final_its_event_dates_and_the_page_announces_them(web_client, harmonogram):
    """``seed_edition_kwantowa`` ustawia dni finału, a harmonogram ogłasza je bez ręcznej poprawki."""
    call_command("seed_edition_kwantowa", "--make-current", verbosity=0)
    final = Stage.objects.get(kind=StageKind.FINAL)

    assert final.event_range == (date(2027, 6, 4), date(2027, 6, 7))
    assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in web_client.get("/harmonogram/").content.decode()


@pytest.mark.parametrize(
    ("opens", "deadline", "expected"),
    [
        # Wspólny miesiąc: zakres jest jednym wyrażeniem, półpauza bez spacji, rok raz.
        ((2027, 6, 4), (2027, 6, 7), "4–7 czerwca 2027"),
        # Różne miesiące: po obu stronach półpauzy stoją całe daty, więc spacje je oddzielają.
        ((2027, 5, 30), (2027, 6, 2), "30 maja – 2 czerwca 2027"),
        # Przełom roku: rok trzeba powtórzyć, bo zakres go przekracza.
        ((2026, 12, 30), (2027, 1, 2), "30 grudnia 2026 – 2 stycznia 2027"),
        # Jeden dzień: nie ma zakresu, jest data.
        ((2027, 6, 4), (2027, 6, 4), "4 czerwca 2027"),
    ],
)
def test_date_range_formatting(opens, deadline, expected):
    assert format_date_range(date(*opens), date(*deadline)) == expected


def test_single_day_onsite_stage_is_not_an_event_range(edition):
    """Etap stacjonarny bez wpisanych dni wydarzenia zostaje przy otwarciu i deadline'ie.

    Miejsce i rodzaj etapu niczego tu nie przesądzają – zjazdem czyni etap dopiero wpis
    „Termin wydarzenia” w panelu.
    """
    warsaw = timezone.get_current_timezone()
    stage = StageFactory(
        edition=edition,
        kind=StageKind.FINAL,
        location="Kraków",
        opens_at=datetime(2027, 6, 4, 9, 0, tzinfo=warsaw),
        deadline_at=datetime(2027, 6, 4, 18, 0, tzinfo=warsaw),
    )

    assert is_onsite_event(stage) is False
    assert stage_rows(edition)[0]["date_range"] == ""


# --- droga z panelu koordynatora ------------------------------------------------------------------


def test_change_in_the_panel_is_visible_on_the_page(web_client, harmonogram, coordinator, edition):
    """Koordynator przesuwa deadline – strona pokazuje nowy termin od razu, bez seeda treści."""
    stage = StageFactory(edition=edition, kind=StageKind.FINAL)
    new_deadline = timezone.localtime(stage.deadline_at) + timedelta(days=9)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{stage.pk}/edit/",
        stage_form_data(stage, deadline_at=new_deadline.strftime(WARSAW_FORMAT)),
    )
    assert response.status_code == 302

    web_client.logout()
    content = web_client.get("/harmonogram/").content.decode()

    assert f"{new_deadline.day} " in content
    assert new_deadline.strftime("%H:%M") in content


def test_event_dates_set_in_the_panel_turn_the_stage_into_a_dated_event(
    web_client, harmonogram, coordinator, edition
):
    """Dopiero wpisane w panelu dni wydarzenia zamieniają dwa terminy w jeden zakres – na obu ekranach.

    Samo miejsce niczego nie zmienia (organizator wpisuje tam także „Tryb zdalny”); zjazd jest
    jawną decyzją koordynatora w polach „Termin wydarzenia”. Rodzaj etapu nie ma tu nic do rzeczy.
    """
    stage = StageFactory(edition=edition, kind=StageKind.FINAL)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{stage.pk}/edit/", stage_form_data(stage, location="Kraków")
    )
    assert response.status_code == 302
    web_client.logout()
    for path in ("/harmonogram/", "/"):
        content = web_client.get(path).content.decode()
        assert "Kraków" in content, path
        assert "<dt>Termin</dt>" not in content, path

    web_client.force_login(coordinator)
    response = web_client.post(
        f"/coordinator/stages/{stage.pk}/edit/",
        stage_form_data(stage, location="Kraków", event_starts_on="2027-06-04", event_ends_on="2027-06-07"),
    )
    assert response.status_code == 302
    web_client.logout()
    # Oba ekrany czytają jedno źródło, więc ogłaszają jedno brzmienie terminu.
    for path in ("/harmonogram/", "/"):
        content = web_client.get(path).content.decode()
        assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in content, path


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
