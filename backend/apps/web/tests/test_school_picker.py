"""Blok wyboru szkoły w formularzach rejestracji (WWW + logowanie społecznościowe).

Sprawdzamy dwie rzeczy: że formularz mapuje cztery pola interfejsu na dwa kwargi serwisu oraz
że strona renderuje komponent tak, jak zakłada ``static/js/school-picker.js`` – łącznie z tym,
że wariant bez JavaScriptu (checkbox + wolny tekst) da się wysłać.
"""

from pathlib import Path

import pytest

from apps.accounts.models import Participant
from apps.schools.tests.factories import SchoolFactory
from apps.web.forms import ParticipantRegisterForm, SocialParticipantSignupForm

from .conftest import captcha_fields, password_fields

# Dostęp do bazy dla **całego** modułu, także dla testów samego formularza: ``CaptchaField.clean``
# kasuje wygasłe wyzwania i szuka swojego w tabeli ``captcha_captchastore``, więc walidacja
# formularza uczestnika przestała być operacją w pamięci (patrz apps/web/captcha.py).
pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"


def form_data(**overrides) -> dict:
    data = {
        **captcha_fields(),
        "email": "nowy@example.test",
        **password_fields(),
        "first_name": "Nowy",
        "last_name": "Uczestnik",
        "district": "mazowieckie",
        "grade": "3",
        "birth_year": 2008,
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        "guardian_consent": "on",
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


def test_typing_in_the_search_box_without_choosing_gets_a_pointed_message():
    """Kto coś wpisał w wyszukiwarkę, jest o krok od celu – komunikat podaje mu obie drogi."""
    form = ParticipantRegisterForm(form_data(school_query="II Liceum"))

    assert form.is_valid() is False
    assert form.errors["school_query"] == [
        "Wybierz szkołę z podpowiedzi albo zaznacz „Mojej szkoły nie ma na liście” i wpisz jej nazwę."
    ]


def test_exception_without_a_name_is_a_field_error():
    form = ParticipantRegisterForm(form_data(school_custom="on", school="   "))

    assert form.is_valid() is False
    assert "school" in form.errors


def test_free_text_without_ticking_the_box_is_accepted():
    """Zgłoszenie z produkcji: wpisana nazwa szkoły ma wystarczyć, także bez zaznaczonej kratki.

    Kratka odsłania pole wolnego tekstu i tyle – kiedy podpowiedzi nie działają (zablokowany
    skrypt), uczestnik widzi pole „Nazwa szkoły” od początku i wypełnia je. Odmowa z powodu
    niezaznaczonej kratki odsyłała go do listy, której akurat u niego nie było.
    """
    form = ParticipantRegisterForm(form_data(school="Wpisane bez zaznaczenia"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["school"] == "Wpisane bez zaznaczenia"
    assert form.cleaned_data["school_id"] is None


def test_a_choice_from_the_list_wins_over_leftover_free_text():
    """Wybór ze słownika ma pierwszeństwo – inaczej w bazie stałyby dwie wersje tej samej szkoły."""
    form = ParticipantRegisterForm(form_data(school_id="17", school="Resztka po wcześniejszym wpisie"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["school_id"] == 17
    assert form.cleaned_data["school"] == ""


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
            "phone": "600 100 200",
            "terms_consent": "on",
            "gdpr_consent": "on",
            "guardian_consent": "on",
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


# --- krok „Miejscowość” -------------------------------------------------------------------------


def test_the_city_is_only_a_search_scope_and_never_reaches_the_service():
    """``school_city`` opisuje **sposób szukania**, a nie szkołę – do serwisu nie ma po co jechać.

    Miasto szkoły wybranej ze słownika przepisuje ``_resolve_school`` z rejestru, a przy szkole
    spoza wykazu nikt nie zapewni, że wpisana obok miejscowość dotyczy tej samej placówki.
    """
    form = ParticipantRegisterForm(form_data(school_city="Wrocław", school_id="17"))

    assert form.is_valid(), form.errors
    assert "school_city" not in form.cleaned_data
    assert form.cleaned_data["school_id"] == 17


def test_the_city_is_optional_and_does_not_block_a_search_by_name():
    """Kto zna nazwę swojej szkoły, nie ma powodu przechodzić przez dwa kroki."""
    form = ParticipantRegisterForm(form_data(school_id="17"))

    assert form.is_valid(), form.errors
    assert form.fields["school_city"].required is False


@pytest.mark.django_db
def test_profile_form_reopens_the_picker_in_the_city_of_the_linked_school(participant):
    """Edycja profilu otwiera się z miejscowością szkoły z rejestru – nie każe szukać od zera."""
    from apps.web.forms import participant_profile_initial

    school = SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE", city="Wrocław")
    participant.school_ref = school
    participant.school = school.name
    participant.save(update_fields=["school_ref", "school"])

    initial = participant_profile_initial(participant)

    assert initial["school_city"] == "Wrocław"


@pytest.mark.django_db
def test_profile_form_of_a_hand_typed_school_has_no_city(participant):
    """Pytamy o nazwę szkoły, nie o adres – podstawione miasto zawęziłoby wyszukiwarkę zmyśleniem."""
    from apps.web.forms import participant_profile_initial

    participant.school_ref = None
    participant.school = "Szkoła Europejska w Brukseli"
    participant.save(update_fields=["school_ref", "school"])

    assert participant_profile_initial(participant)["school_city"] == ""


# --- render -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_registration_page_renders_the_picker(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert "data-school-picker" in body
    assert 'data-search-url="/api/schools/"' in body
    assert 'data-cities-url="/api/schools/cities/"' in body
    assert 'data-district-field="id_district"' in body
    # Punkty zaczepienia skryptu – nazwy muszą zgadzać się z static/js/school-picker.js.
    for hook in (
        "city",
        "city-list",
        "city-status",
        "query",
        "school-id",
        "custom",
        "free",
        "free-input",
        "list",
        "status",
    ):
        assert f'data-picker="{hook}"' in body, hook
    assert 'role="listbox"' in body
    assert "Mojej szkoły nie ma na liście" in body
    # Django dokleja do kontrolki ``aria-describedby="<auto_id>_helptext"``; blok renderujemy
    # ręcznie, więc identyfikator podpowiedzi musi istnieć – inaczej atrybut wskazuje na nic.
    for field in ("city", "query"):
        assert f'aria-describedby="id_school_{field}_helptext"' in body
        assert f'id="id_school_{field}_helptext"' in body


@pytest.mark.django_db
def test_registration_page_asks_for_the_city_before_the_school(web_client, edition):
    """Uwaga organizatora z 16.09: „może warto dodać pole miasta i wówczas dać pełną listę szkół”.

    Kolejność w DOM jest tu treścią, a nie układem: pole miejscowości ma stać **przed** polem
    szkoły, bo zawęża jego listę. Odwrotna kolejność kazałaby najpierw szukać w całym kraju.
    """
    body = web_client.get(REGISTER_URL).content.decode()

    assert 'id="id_school_city"' in body
    assert body.index('id="id_school_city"') < body.index('id="id_school_query"')
    assert "Miejscowość" in body
    assert "pokaże wtedy pełną listę szkół z tej miejscowości" in body


@pytest.mark.django_db
def test_the_picker_does_not_depend_on_alpine(web_client, edition):
    """Blok nie ma ani jednego atrybutu Alpine'a: skrypt jest czystym JS-em i nie potrzebuje CDN-u.

    To jest sedno poprawki po zgłoszeniu z produkcji – wersja na Alpine padała w całości, gdy
    proxy albo wtyczka blokowały jeden zewnętrzny adres.
    """
    body = web_client.get(REGISTER_URL).content.decode()
    block = body.split('class="school-picker"')[1].split("</div>")[0]

    assert "x-data" not in block
    assert "x-ref" not in block
    assert "x-on:" not in block


@pytest.mark.django_db
def test_registration_page_loads_the_picker_script_with_a_nonce(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert "js/school-picker.js" in body
    script = next(line for line in body.splitlines() if "js/school-picker.js" in line)
    assert 'nonce="' in script
    # ``defer``, bo skrypt szuka swoich elementów w gotowym dokumencie – i **nie** ``src`` spoza
    # serwisu: cały sens przepisania na czysty JS polega na tym, że nie ma tu adresu z CDN-u.
    assert "defer" in script
    assert "//" not in script.split("src=")[1]


def test_the_stylesheet_makes_the_hidden_attribute_win():
    """Bez tej reguły ``hidden`` w bloku „szkoła” nic nie chowa – i to jest połowa zgłoszenia.

    ``[hidden]`` z arkusza przeglądarki ma zerową swoistość, więc ``.form p { display: flex }``
    je bije: skrypt ustawiał atrybut, a pole „Nazwa szkoły” stało otwarte, uczestnik je wypełniał
    i dostawał odmowę. Sprawdzamy arkusz, bo tego jednego nie widać w żadnym teście renderu.
    """
    css = (Path(__file__).resolve().parents[3] / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "[hidden] {\n  display: none !important;\n}" in css


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
