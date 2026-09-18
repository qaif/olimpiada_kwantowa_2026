"""Blok „szkoła” przy dwóch wykazach i przy wyborze rodzaju placówki (§ 1.3.3, zadanie T25).

Test dotyczy **umowy między szablonem a skryptem**, a nie samego skryptu: przeglądarki tu nie ma
(chodzi po niej ``e2e/check_school_picker.py``), więc sprawdzamy to, co da się sprawdzić bez niej –
że punkty zaczepienia w HTML-u nazywają się tak, jak szuka ich ``static/js/school-picker.js``,
i że **bez profilu rejestracji nie ma ich wcale**.

Reguła, która stoi za każdym z tych testów, jest jedna (§ 0.1): ten sam plik obsługuje cztery
ekrany, a Konkurs #1 nie ma ani listy rodzajów placówek, ani drugiego wykazu – jego ``/register/``
ma więc wyjść z szablonu znak w znak taki, jak przed etapem 2.
"""

from pathlib import Path

import pytest
from django import forms
from django.template.loader import render_to_string

from apps.accounts.models import RegistrationProfile
from apps.accounts.services import REGISTRATION_PROFILE_FLAG
from apps.schools.models import InstitutionType
from apps.web.forms import SchoolChoiceMixin

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"

#: Plik skryptu – czytamy go jak arkusz w ``test_school_picker.py``: nazwa punktu zaczepienia jest
#: umową dwóch plików i nie da się jej sprawdzić w żadnym renderze.
PICKER_JS = Path(__file__).resolve().parents[3] / "static" / "js" / "school-picker.js"


def profile_for(competition, **fields) -> RegistrationProfile:
    """Profil **i** flaga – bez flagi wiersz nie zmienia niczego (§ 0.1)."""
    row = RegistrationProfile.objects.create(competition=competition, **fields)
    competition.feature_flags = {**(competition.feature_flags or {}), REGISTRATION_PROFILE_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return row


class PickerFormWithCustomDirectory(SchoolChoiceMixin):
    """Formularz z **drugą** kolumną dowiązania – taki, jaki ma konkurs ze słownikiem własnym.

    Definiujemy go w teście, a nie sięgamy po formularz rejestracji, bo przedmiotem jest szablon:
    blok ma renderować ukryte pole ``custom_institution_id`` **wtedy i tylko wtedy**, gdy
    formularz je ma. Pole, którego nie zna ``clean()``, nie dojechałoby do serwisu rejestracji,
    a niewypełniana kontrolka obiecywałaby coś, czego nikt nie odbiera.
    """

    district = forms.CharField(required=False)
    custom_institution_id = forms.IntegerField(
        required=False, widget=forms.HiddenInput(attrs={"data-picker": "custom-institution-id"})
    )


class PickerFormWithOneDirectory(SchoolChoiceMixin):
    """Dzisiejszy blok: jeden wykaz, jedna kolumna dowiązania."""

    district = forms.CharField(required=False)


# --- szablon ------------------------------------------------------------------------------------


def test_the_block_renders_the_second_link_column_when_the_form_has_it(competition):
    html = render_to_string("web/_school_picker.html", {"form": PickerFormWithCustomDirectory()})

    assert 'data-picker="custom-institution-id"' in html
    assert 'name="custom_institution_id"' in html


def test_the_block_without_the_second_column_is_byte_for_byte_todays_block(competition):
    """Najważniejszy test tego pliku: dopisek jest **warunkowy**, razem ze spacjami.

    Porównujemy cały wynik renderu z wynikiem sprzed zmiany odtworzonym z tego samego szablonu –
    gdyby ``{% if %}`` zostawiał po sobie choćby spację albo pusty wiersz, HTML ``/register/``
    Konkursu #1 przestałby być dzisiejszy, a to jest punkt 2 listy zamrożonych rzeczy (§ 0.2).
    """
    form = PickerFormWithOneDirectory()
    html = render_to_string("web/_school_picker.html", {"form": form})

    assert "custom-institution-id" not in html
    assert "custom_institution_id" not in html
    assert "data-institution-type" not in html
    # Wiersz z ukrytym polem to **samo** pole i dwie spacje wcięcia – ani jednego znaku więcej.
    assert next(line for line in html.splitlines() if 'name="school_id"' in line) == f"  {form['school_id']}"
    # Tak samo korzeń bloku: ostatnim atrybutem zostaje ten, który stał tam przed etapem 2.
    root = next(line for line in html.splitlines() if "data-district-field" in line)
    assert root == f'     data-district-field="{form["district"].auto_id}">'


def test_the_root_points_at_the_institution_type_field(client_for, competition, edition):
    """Skrypt szuka listy wyboru po identyfikatorze – tak samo, jak szuka województwa."""
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.UNIVERSITY],
    )

    body = client_for(competition).get(REGISTER_URL).content.decode()

    assert 'data-institution-type="id_institution_type"' in body
    assert 'data-picker="institution-type"' in body
    # Rodzaj placówki stoi **przed** blokiem, bo to on rozstrzyga, czego w nim szukać
    # (``extended_participant_field_order``) – skrypt sięga po niego z zewnątrz, po identyfikatorze.
    assert body.index('data-picker="institution-type"') < body.index("data-school-picker")


def test_without_a_profile_the_registration_page_has_no_new_hook(client_for, competition, edition):
    body = client_for(competition).get(REGISTER_URL).content.decode()

    assert "data-institution-type" not in body
    assert "custom-institution-id" not in body
    assert "data-school-picker" in body


def test_a_single_allowed_type_leaves_the_block_exactly_as_it_is(client_for, competition, edition):
    """Lista wyboru z jedną pozycją nie jest pytaniem, więc nie ma jej – a z nią hooka skryptu."""
    profile_for(competition, allowed_institution_types=[InstitutionType.UNIVERSITY])

    body = client_for(competition).get(REGISTER_URL).content.decode()

    assert "data-institution-type" not in body
    assert 'data-picker="query"' in body


# --- umowa ze skryptem --------------------------------------------------------------------------


def test_the_script_knows_both_hooks_and_both_sources():
    """Nazwy z szablonu i z odpowiedzi API muszą stać w skrypcie dosłownie – inaczej nic nie łączy.

    Czytamy plik, bo to jedyny sposób: przeglądarki w tej suicie nie ma, a rozjazd nazwy hooka
    objawiłby się dopiero na produkcji, jako okno wyboru, które przestało podpowiadać.
    """
    source = PICKER_JS.read_text(encoding="utf-8")

    assert 'ref(root, "custom-institution-id")' in source
    assert "root.dataset.institutionType" in source
    assert 'params.set("institution_type"' in source
    # Rodzaje placówek mające wiersze w wykazach – odpowiednik ``DIRECTORY_INSTITUTION_TYPES``.
    assert 'var DIRECTORY_TYPES = ["PRIMARY", "SECONDARY", "UNIVERSITY"];' in source


def test_the_script_mirrors_the_server_list_of_directory_types():
    """Powtórzona stała ma być **równa** oryginałowi – inaczej blok chowałby się nie tam, gdzie trzeba."""
    from apps.accounts.models import DIRECTORY_INSTITUTION_TYPES

    source = PICKER_JS.read_text(encoding="utf-8")
    declared = ", ".join(f'"{value}"' for value in DIRECTORY_INSTITUTION_TYPES)

    assert f"var DIRECTORY_TYPES = [{declared}];" in source
