"""Ekran „Profil rejestracji” i ukryte pole drugiego wykazu w formularzu (etap 2, T23).

Dwie sprawy, jedna decyzja organizatora – dlatego jeden plik:

- **ekran** ``/coordinator/registration-profile/`` jest za flagą ``institution_types`` i bez niej
  daje **404** (§ 2.1): przy wyłączonej fladze ``registration_profile()`` oddaje wartości domyślne
  niezależnie od zawartości wiersza, więc formularz pozwalający coś zmienić obiecywałby zmianę,
  której ``/register/`` nie zobaczy. Wiersz **powstaje dopiero przy pierwszym zapisie** – brak
  wiersza i wiersz z domyślnymi są równoważne (§ 1.3.4), a wpis audytowy niesie **same nazwy
  zmienionych pól**,
- **pole** ``custom_institution_id`` dochodzi do ``SchoolChoiceMixin`` wtedy i tylko wtedy, gdy
  konkurs korzysta ze słownika własnego (``custom_directory_enabled``: flaga **i** pole profilu).
  Konkurs #1 nie dostaje ani pola, ani jednego zapytania więcej – to jest punkt 2 listy
  zamrożonych rzeczy (§ 0.2) i budżet ``/register/`` z § 5.6.

Adresy ekranu są w ``apps/web/urls.py``: montaż wydania H rozwinął tam
``urls_institutions.urlpatterns``, więc testy chodzą po mapie produkcyjnej.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import CompetitionRole, RegistrationProfile
from apps.accounts.services import (
    CUSTOM_DIRECTORY_FLAG,
    REGISTRATION_PROFILE_FLAG,
    custom_directory_enabled,
    registration_profile,
)
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.schools.custom import CustomInstitution
from apps.schools.models import InstitutionType
from apps.tenancy.tests.factories import grant_membership
from apps.web.forms import (
    CUSTOM_INSTITUTION_FIELD,
    SCHOOL_FIELD_NAMES,
    ParticipantProfileForm,
    ParticipantRegisterForm,
)

from .conftest import captcha_fields, password_fields

pytestmark = pytest.mark.django_db

PROFILE_URL = "/coordinator/registration-profile/"
REGISTER_URL = "/register/"


def enable(competition, *flags: str):
    competition.feature_flags = {**(competition.feature_flags or {}), **dict.fromkeys(flags, True)}
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client_for, competition):
    enable(competition, REGISTRATION_PROFILE_FLAG)
    return coordinator_for(client_for, competition)


def profile_payload(**overrides) -> dict:
    data = {
        "allowed_institution_types": [InstitutionType.SECONDARY, InstitutionType.UNIVERSITY],
        "allow_free_text_school": "on",
        "require_grade": "on",
        "require_phone": "on",
        "require_region": "on",
        "require_birth_year": "on",
        "grade_min": "",
        "grade_max": "",
    }
    data.update(overrides)
    return data


# --- przełącznik ekranu -----------------------------------------------------------------------


def test_the_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    client = coordinator_for(client_for, competition)

    assert client.get(PROFILE_URL).status_code == 404
    assert client.post(PROFILE_URL, profile_payload()).status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory().user)

    assert client.get(PROFILE_URL).status_code == 403

    enable(competition, REGISTRATION_PROFILE_FLAG)

    assert client.get(PROFILE_URL).status_code == 403


# --- ekran ------------------------------------------------------------------------------------


def test_the_screen_shows_todays_rules_before_the_first_save(coordinator_client, competition):
    response = coordinator_client.get(PROFILE_URL)

    assert response.status_code == 200
    assert response.context["form"].instance.pk is None
    assert RegistrationProfile.objects.count() == 0
    assert response.context["card"]["types"] == ["szkoła ponadpodstawowa"]


def test_the_first_save_creates_the_row(coordinator_client, competition):
    response = coordinator_client.post(PROFILE_URL, profile_payload(allow_custom_directory="on"))

    assert response.status_code == 302
    row = RegistrationProfile.objects.get(competition=competition)
    assert row.allowed_institution_types == [InstitutionType.SECONDARY, InstitutionType.UNIVERSITY]
    assert row.allow_custom_directory is True


def test_the_audit_entry_carries_field_names_and_no_values(coordinator_client, competition):
    coordinator_client.post(PROFILE_URL, profile_payload(allow_foreign="on"))

    entry = AuditLog.objects.get(action="registration_profile.updated")
    assert "allow_foreign" in entry.diff["fields"]
    assert "allowed_institution_types" in entry.diff["fields"]
    assert set(entry.diff) == {"fields"}


def test_an_upside_down_grade_range_is_refused_under_the_field(coordinator_client, competition):
    response = coordinator_client.post(PROFILE_URL, profile_payload(grade_min="4", grade_max="2"))

    assert response.status_code == 400
    assert response.context["form"].errors["grade_max"] == [
        "Klasa „do” nie może być mniejsza niż klasa „od”."
    ]
    assert RegistrationProfile.objects.count() == 0


def test_what_the_screen_saves_is_what_the_registration_form_reads(coordinator_client, competition):
    coordinator_client.post(PROFILE_URL, profile_payload())

    assert registration_profile(competition).institution_types() == (
        InstitutionType.SECONDARY,
        InstitutionType.UNIVERSITY,
    )
    assert "institution_type" in ParticipantRegisterForm().fields


def test_an_empty_set_of_types_means_todays_form(coordinator_client, competition):
    """Pusty zbiór znaczy „szkoła ponadpodstawowa”, a nie „żadna” – inaczej byłaby to awaria."""
    coordinator_client.post(PROFILE_URL, profile_payload(allowed_institution_types=[]))

    row = RegistrationProfile.objects.get(competition=competition)
    assert row.allowed_institution_types == []
    assert row.institution_types() == (InstitutionType.SECONDARY,)


# --- pole drugiego wykazu w formularzu rejestracji ---------------------------------------------


def directory_on(competition) -> RegistrationProfile:
    """Flaga **i** pole profilu – koniunkcja, o której mówi ``custom_directory_enabled``."""
    row = RegistrationProfile.objects.create(competition=competition, allow_custom_directory=True)
    enable(competition, CUSTOM_DIRECTORY_FLAG)
    return row


def test_competition_one_has_no_second_link_column(competition):
    """Konkurs #1: ani pola w formularzu, ani śladu w HTML-u ``/register/``."""
    form = ParticipantRegisterForm()

    assert CUSTOM_INSTITUTION_FIELD not in form.fields
    assert form.school_field_names == SCHOOL_FIELD_NAMES


def test_a_row_without_the_flag_adds_nothing(competition):
    RegistrationProfile.objects.create(competition=competition, allow_custom_directory=True)

    assert CUSTOM_INSTITUTION_FIELD not in ParticipantRegisterForm().fields


def test_the_flag_without_the_profile_field_adds_nothing(competition):
    enable(competition, CUSTOM_DIRECTORY_FLAG)

    assert custom_directory_enabled(competition) is False
    assert CUSTOM_INSTITUTION_FIELD not in ParticipantRegisterForm().fields


def test_the_field_appears_when_the_competition_uses_the_dictionary(competition):
    directory_on(competition)

    form = ParticipantRegisterForm()

    assert CUSTOM_INSTITUTION_FIELD in form.fields
    # Blok renderuje je ręcznie razem z ``school_id`` – bez tego wyszłoby w HTML-u dwa razy.
    assert form.school_field_names == (*SCHOOL_FIELD_NAMES, CUSTOM_INSTITUTION_FIELD)


def test_the_profile_edit_screen_does_not_get_the_field(competition):
    """``/me/profile/`` drugiego wykazu nie zapisuje, więc nie ma tam czego wybierać."""
    directory_on(competition)

    assert CUSTOM_INSTITUTION_FIELD not in ParticipantProfileForm().fields


def test_the_registration_page_renders_the_hidden_field_exactly_once(client_for, competition, edition):
    directory_on(competition)

    content = client_for(competition).get(REGISTER_URL).content.decode()

    assert content.count('name="custom_institution_id"') == 1
    assert 'data-picker="custom-institution-id"' in content


def test_the_choice_goes_through_clean_the_way_school_id_does(competition):
    directory_on(competition)
    row = CustomInstitution.objects.create(
        competition=competition, name="Uniwersytet Partnerski", city="Kraków"
    )

    form = ParticipantRegisterForm(
        {
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
            "school_query": "Uniwersytet",
            CUSTOM_INSTITUTION_FIELD: row.pk,
        }
    )

    assert form.is_valid(), form.errors
    # Klucz jedzie do serwisu tak samo jak ``school_id`` – ``register_participant`` zna go z nazwy.
    assert form.cleaned_data[CUSTOM_INSTITUTION_FIELD] == row.pk
    # Obie drogi wykazu publicznego wyzerowane: nazwę przepisze serwis z wybranego wiersza.
    assert form.cleaned_data["school_id"] is None
    assert form.cleaned_data["school"] == ""


def test_an_empty_hidden_field_leaves_todays_message(competition):
    """Bez wyboru z żadnej listy komunikat zostaje dzisiejszy – pole niczego nie przesłania."""
    directory_on(competition)

    form = ParticipantRegisterForm(
        {
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
            "school_query": "LO",
            CUSTOM_INSTITUTION_FIELD: "",
        }
    )

    assert not form.is_valid()
    assert form.errors["school_query"] == [
        "Wybierz szkołę z podpowiedzi albo zaznacz „Mojej szkoły nie ma na liście” i wpisz jej nazwę."
    ]


def test_competition_one_does_not_pay_a_single_query_for_the_condition(competition):
    """Warunek przy wyłączonej fladze odpowiada **bez dotknięcia bazy** (§ 5.6)."""
    ParticipantRegisterForm()  # rozgrzewka: zgody i nazwa organizatora idą z bazy raz na proces

    with CaptureQueriesContext(connection) as queries:
        ParticipantRegisterForm()

    assert not [entry for entry in queries.captured_queries if "custominstitution" in entry["sql"].lower()]
    assert not [entry for entry in queries.captured_queries if "registrationprofile" in entry["sql"].lower()]
