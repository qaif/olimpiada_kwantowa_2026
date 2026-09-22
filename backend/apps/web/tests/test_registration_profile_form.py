"""Formularz ``/register/`` sterowany profilem rejestracji (§ 1.3.4, § 2.3).

Dwie rzeczy są tu przedmiotem testu:

- **bez profilu formularz jest dzisiejszy.** Nie „prawie dzisiejszy”: ta sama lista pól, ta sama
  kolejność, te same komunikaty. Porównujemy nazwy i kolejność pól, a nie cały dokument –
  dokument zmienia się przy każdej poprawce arkusza i test byłby alarmem, którego nikt nie czyta
  (§ 5.3);
- **z profilem formularz pyta dokładnie o to, co profil dopuszcza.** Rodzaj placówki pojawia się
  tylko wtedy, gdy jest z czego wybierać; blok szkoły znika, gdy żaden dopuszczony rodzaj nie ma
  wierszy w wykazie; nazwa placówki i kraj pojawiają się przy placówkach spoza wykazu.

Czego tu **nie** ma: treści zgód (``apps/tenancy/tests/test_branding.py``) ani zachowania skryptu
podpowiedzi (``apps/web/tests/test_school_picker.py``, ``e2e/check_school_picker.py``).
"""

import pytest

from apps.accounts.models import RegistrationProfile
from apps.accounts.services import REGISTRATION_PROFILE_FLAG
from apps.schools.models import InstitutionType
from apps.web.forms import (
    PARTICIPANT_FIELD_ORDER,
    SCHOOL_FIELD_NAMES,
    ParticipantProfileForm,
    ParticipantRegisterForm,
)

from .conftest import captcha_fields, password_fields

# Baza dla całego modułu: ``CaptchaField.clean`` szuka swojego wyzwania w tabeli, więc walidacja
# formularza uczestnika nie jest operacją w pamięci (patrz apps/web/captcha.py).
pytestmark = pytest.mark.django_db


def enable(competition) -> None:
    competition.feature_flags = {**(competition.feature_flags or {}), REGISTRATION_PROFILE_FLAG: True}
    competition.save(update_fields=["feature_flags"])


def profile_for(competition, **fields) -> RegistrationProfile:
    """Profil **i** flaga – bez flagi wiersz nie zmienia niczego i taki jest sens § 0.1."""
    row = RegistrationProfile.objects.create(competition=competition, **fields)
    enable(competition)
    return row


def form_data(**overrides) -> dict:
    data = {
        **captcha_fields(),
        "email": "nowy@example.test",
        **password_fields(),
        "first_name": "Nowy",
        "last_name": "Uczestnik",
        "district": "mazowieckie",
        "grade": "3",
        "birth_date": "2008-12-31",
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        "guardian_consent": "on",
    }
    data.update(overrides)
    return data


# --- bez profilu ---------------------------------------------------------------------------------


def test_without_a_profile_the_field_list_is_unchanged(competition):
    """Lista pól i ich kolejność co do joty – to jest ``test_registration_form_html_unchanged``.

    ``PARTICIPANT_FIELD_ORDER`` jest deklaracją modułu; formularz ma ją odtwarzać bez ani jednego
    pola więcej. Pola antyspamowe stoją na końcu i nie należą do tej krotki, więc porównujemy jej
    prefiks – tak samo, jak robi to szablon, który blok antyspamowy renderuje osobno.
    """
    form = ParticipantRegisterForm()

    names = list(form.fields)
    assert names[: len(PARTICIPANT_FIELD_ORDER)] == list(PARTICIPANT_FIELD_ORDER)
    assert "institution_type" not in form.fields
    assert "country" not in form.fields
    assert "institution_name" not in form.fields


def test_without_a_profile_the_school_messages_are_unchanged(competition):
    form = ParticipantRegisterForm(form_data(school_query="LO", school=""))

    assert not form.is_valid()
    assert form.errors["school_query"] == [
        "Wybierz szkołę z podpowiedzi albo zaznacz „Mojej szkoły nie ma na liście” i wpisz jej nazwę."
    ]


def test_a_row_without_the_flag_changes_nothing(competition):
    """Wiersz w bazie bez flagi jest niewidoczny – przełącznik jest jeden (§ 0.1)."""
    RegistrationProfile.objects.create(
        competition=competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    form = ParticipantRegisterForm()

    assert "institution_type" not in form.fields
    assert list(form.fields)[: len(PARTICIPANT_FIELD_ORDER)] == list(PARTICIPANT_FIELD_ORDER)


def test_the_profile_edit_screen_does_not_follow_the_profile(competition):
    """``/me/profile/`` zostaje przy dzisiejszych regułach – jego zapis innych nie zna."""
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    form = ParticipantProfileForm()

    assert "institution_type" not in form.fields
    assert "country" not in form.fields


# --- z profilem ----------------------------------------------------------------------------------


def test_the_type_of_institution_stands_in_front_of_the_school_block(competition):
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    names = list(ParticipantRegisterForm().fields)

    assert names.index("institution_type") == names.index(SCHOOL_FIELD_NAMES[0]) - 1
    # Kraj i nazwa placówki stoją **za** blokiem szkoły, a zgody nadal na końcu.
    assert names.index("country") > names.index(SCHOOL_FIELD_NAMES[-1])
    assert names.index("institution_name") > names.index("country")
    assert names.index("grade") > names.index("institution_name")
    assert names.index("gdpr_consent") > names.index("grade")


def test_one_allowed_type_means_no_choice_field(competition):
    """Lista wyboru z jedną pozycją nie jest pytaniem, tylko ozdobą."""
    profile_for(competition, allowed_institution_types=[InstitutionType.UNIVERSITY])

    assert "institution_type" not in ParticipantRegisterForm().fields


def test_without_a_directory_type_the_school_block_disappears(competition):
    """Sam ``NONE``: nie ma czego szukać w wykazie, więc bloku szkoły nie ma w formularzu."""
    profile_for(competition, allowed_institution_types=[InstitutionType.NONE])

    form = ParticipantRegisterForm(form_data())

    assert all(name not in form.fields for name in SCHOOL_FIELD_NAMES)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["school"] == ""
    assert form.cleaned_data["school_id"] is None


def test_a_foreign_institution_asks_for_the_name_and_the_country(competition):
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    form = ParticipantRegisterForm(form_data(institution_type=InstitutionType.FOREIGN))

    assert not form.is_valid()
    assert form.errors["institution_name"] == ["Podaj nazwę placówki."]
    assert form.errors["country"] == ["Podaj kraj."]


def test_a_foreign_institution_maps_to_service_kwargs(competition):
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    form = ParticipantRegisterForm(
        form_data(
            institution_type=InstitutionType.FOREIGN,
            institution_name="Gymnasium Berlin",
            country="DE",
            school_id="17",
        )
    )

    assert form.is_valid(), form.errors
    # Wybór z wykazu nie przeżywa zmiany rodzaju placówki – liczy się ostatnia decyzja uczestnika.
    assert form.cleaned_data["school_id"] is None
    assert form.cleaned_data["school"] == ""
    assert form.cleaned_data["institution_name"] == "Gymnasium Berlin"
    assert "school_query" not in form.cleaned_data
    assert "school_custom" not in form.cleaned_data


def test_closing_the_free_text_door_removes_the_checkbox_and_says_so(competition):
    profile_for(competition, allow_free_text_school=False)

    form = ParticipantRegisterForm(form_data(school_query="LO"))

    assert "school_custom" not in form.fields
    assert "school" not in form.fields
    assert not form.is_valid()
    assert form.errors["school_query"] == ["Wybierz placówkę z listy."]


def test_the_grade_choices_come_from_the_profile(competition):
    profile_for(competition, grade_min=1, grade_max=8)

    values = [value for value, _ in ParticipantRegisterForm().fields["grade"].choices if value]

    assert values == [str(number) for number in range(1, 9)]


def test_a_competition_can_stop_asking_for_the_phone(competition):
    profile_for(competition, require_phone=False)

    form = ParticipantRegisterForm(form_data(phone="", school="LO nr 1"))

    assert form.fields["phone"].required is False
    assert form.is_valid(), form.errors


# --- strona --------------------------------------------------------------------------------------


def test_the_page_renders_the_new_fields(client_for, competition, edition):
    """Pola profilu idą zwykłą pętlą szablonu – bez ani jednej zmiany w ``_school_picker.html``."""
    profile_for(
        competition,
        allowed_institution_types=[InstitutionType.SECONDARY, InstitutionType.FOREIGN],
        allow_foreign=True,
    )

    response = client_for(competition).get("/register/")
    content = response.content.decode()

    assert response.status_code == 200
    assert 'name="institution_type"' in content
    assert 'name="institution_name"' in content
    assert 'data-picker="institution-type"' in content
