"""Blok wyboru szkoły w formularzach rejestracji (WWW + logowanie społecznościowe).

Sprawdzamy dwie rzeczy: że formularz mapuje cztery pola interfejsu na dwa kwargi serwisu oraz
że strona renderuje komponent tak, jak zakłada ``static/js/school-picker.js`` – łącznie z tym,
że wariant bez JavaScriptu (checkbox + wolny tekst) da się wysłać.
"""

import pytest

from apps.accounts.models import Participant
from apps.schools.tests.factories import SchoolFactory
from apps.web.forms import ParticipantRegisterForm, SocialParticipantSignupForm

REGISTER_URL = "/register/"


def form_data(**overrides) -> dict:
    data = {
        "email": "nowy@example.test",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Nowy",
        "last_name": "Uczestnik",
        "district": "mazowieckie",
        "grade": "3",
        "birth_year": 2008,
        "gdpr_consent": "on",
    }
    data.update(overrides)
    return data


# --- formularz --------------------------------------------------------------------------------


def test_choosing_from_the_directory_maps_to_service_kwargs():
    form = ParticipantRegisterForm(form_data(school_id="17"))

    assert form.is_valid(), form.errors
    # Do serwisu jadą wyłącznie klucze, które zna ``register_participant``.
    assert form.cleaned_data["school_id"] == 17
    assert form.cleaned_data["school"] == ""
    assert "school_query" not in form.cleaned_data
    assert "school_custom" not in form.cleaned_data
    assert form.cleaned_data["grade"] == 3


def test_declared_exception_maps_to_free_text():
    form = ParticipantRegisterForm(form_data(school_custom="on", school="Lycée Français"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["school"] == "Lycée Français"
    assert form.cleaned_data["school_id"] is None


def test_exception_wins_over_a_stale_choice():
    """Zaznaczony wyjątek unieważnia wcześniej wybraną szkołę – liczy się ostatnia decyzja."""
    form = ParticipantRegisterForm(form_data(school_id="17", school_custom="on", school="Szkoła w Wilnie"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["school_id"] is None
    assert form.cleaned_data["school"] == "Szkoła w Wilnie"


def test_neither_path_filled_is_a_field_error():
    form = ParticipantRegisterForm(form_data())

    assert form.is_valid() is False
    assert "Wybierz szkołę z listy albo zaznacz, że nie ma jej na liście." in form.errors["school_query"]


def test_exception_without_a_name_is_a_field_error():
    form = ParticipantRegisterForm(form_data(school_custom="on", school="   "))

    assert form.is_valid() is False
    assert "school" in form.errors


def test_free_text_typed_without_ticking_the_box_is_ignored():
    """Samo wpisanie czegoś w pole wolnego tekstu nie może obchodzić wyboru ze słownika."""
    form = ParticipantRegisterForm(form_data(school="Wpisane bez zaznaczenia"))

    assert form.is_valid() is False
    assert "school_query" in form.errors


def test_grade_is_required_and_bounded():
    assert ParticipantRegisterForm(form_data(school_id="17", grade="")).is_valid() is False
    assert ParticipantRegisterForm(form_data(school_id="17", grade="6")).is_valid() is False


def test_social_form_shares_the_school_block():
    form = SocialParticipantSignupForm(
        {
            "first_name": "Anna",
            "last_name": "Nowak",
            "district": "mazowieckie",
            "grade": "1",
            "birth_year": 2008,
            "gdpr_consent": "on",
            "school_custom": "on",
            "school": "Szkoła Europejska w Brukseli",
        }
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["school"] == "Szkoła Europejska w Brukseli"
    # Formularz społecznościowy nie pyta o e-mail ani hasło – przychodzą od dostawcy.
    assert "email" not in form.fields
    assert "password" not in form.fields


def test_field_order_puts_the_voivodeship_before_the_school():
    """Podpowiedzi zawężają się do województwa, więc w DOM musi ono stać wcześniej."""
    names = list(ParticipantRegisterForm().fields)

    assert names.index("district") < names.index("school_id")
    assert names.index("school_id") < names.index("grade")


# --- render -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_registration_page_renders_the_picker(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert 'x-data="schoolPicker"' in body
    assert 'data-search-url="/api/schools/"' in body
    assert 'data-district-field="id_district"' in body
    # Punkty zaczepienia komponentu – nazwy muszą zgadzać się z static/js/school-picker.js.
    for ref in ("query", "schoolId", "custom", "free", "list", "status"):
        assert f'x-ref="{ref}"' in body, ref
    assert 'role="listbox"' in body
    assert "Mojej szkoły nie ma na liście" in body


@pytest.mark.django_db
def test_registration_page_loads_the_picker_script_with_a_nonce(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert "js/school-picker.js" in body
    script = next(line for line in body.splitlines() if "js/school-picker.js" in line)
    assert 'nonce="' in script
    # Skrypt musi stać przed znacznikiem Alpine'a: build CDN startuje w mikrozadaniu tuż po
    # swoim wykonaniu, więc rejestracja komponentu doklejona później nigdy by się nie odbyła.
    assert body.index("js/school-picker.js") < body.index("@alpinejs/csp")


@pytest.mark.django_db
def test_page_without_javascript_can_still_be_submitted(web_client, edition):
    """Wariant bez JS: pole wolnego tekstu jest w HTML-u widoczne, a POST zakłada konto."""
    body = web_client.get(REGISTER_URL).content.decode()
    assert 'class="school-picker__free"' in body
    assert "hidden" not in body.split('class="school-picker__free"')[1].split(">")[0]

    response = web_client.post(REGISTER_URL, form_data(school_custom="on", school="Szkoła spoza wykazu"))

    assert response.status_code == 302
    assert Participant.objects.get(user__email="nowy@example.test").school == "Szkoła spoza wykazu"


@pytest.mark.django_db
def test_registration_through_the_directory_links_the_row(web_client, edition):
    school = SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE", city="Warszawa")

    response = web_client.post(REGISTER_URL, form_data(school_id=str(school.id)))

    assert response.status_code == 302
    participant = Participant.objects.get(user__email="nowy@example.test")
    assert participant.school_ref == school
    assert participant.school == "XIV LICEUM OGÓLNOKSZTAŁCĄCE"
    assert participant.grade == 3


@pytest.mark.django_db
def test_unknown_school_id_comes_back_as_a_form_error(web_client, edition):
    response = web_client.post(REGISTER_URL, form_data(school_id="999999"))

    assert response.status_code == 200
    assert "Wybrana szkoła nie istnieje w rejestrze." in response.content.decode()
    assert not Participant.objects.exists()
