"""Rejestracja na placówkę ze słownika organizatora (§ 1.3.3, gałąź ``_resolve_institution``).

Cztery zdania, których ta gałąź ma dowieść:

- **bez flagi nie ma jej wcale.** Przysłany ``custom_institution_id`` jest przy wyłączonej fladze
  ``custom_school_directory`` ignorowany i nie kosztuje ani jednego zapytania (§ 5.6) – rejestracja
  Konkursu #1 przechodzi tędy dokładnie tak, jak przed etapem 2,
- **wybór ze słownika wypełnia ``school``** tą samą nazwą, którą widzi uczestnik – tak jak przy
  wykazie publicznym. Dzięki temu publikacja wyników i próg k-anonimowości nie muszą wiedzieć,
  z którego wykazu placówka pochodzi (decyzja D13),
- **wiersz cudzego konkursu jest nie do odróżnienia od nieistniejącego** – lista placówek
  organizatora jest jego listą kontrahentów,
- **rodzaj placówki obowiązuje tak samo**, jak w wykazie publicznym, i odmawia tym samym zdaniem.
"""

import pytest

from apps.accounts.models import Participant, RegistrationProfile
from apps.accounts.services import (
    CUSTOM_DIRECTORY_FLAG,
    REGISTRATION_PROFILE_FLAG,
    _resolve_institution,
    register_participant,
    registration_profile,
)
from apps.core.api import DomainError
from apps.schools.custom import CustomInstitution
from apps.schools.models import InstitutionType

pytestmark = pytest.mark.django_db


def enable(competition, *flags) -> None:
    """Włącza flagi zapisem do ``feature_flags`` – przełącznik siedzi w danych konkursu."""
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])


def with_directory(competition, **profile_fields):
    """Konkurs, który słownika własnego używa: wiersz profilu **i** obie flagi."""
    fields = {"allow_custom_directory": True, "allowed_institution_types": [InstitutionType.UNIVERSITY]}
    fields.update(profile_fields)
    RegistrationProfile.objects.create(competition=competition, **fields)
    enable(competition, REGISTRATION_PROFILE_FLAG, CUSTOM_DIRECTORY_FLAG)
    return competition


def institution(competition, **fields) -> CustomInstitution:
    data = {
        "name": "UNIWERSYTET TESTOWY",
        "city": "Kraków",
        "institution_type": InstitutionType.UNIVERSITY,
    }
    data.update(fields)
    return CustomInstitution.objects.create(competition=competition, **data)


def kwargs(**overrides) -> dict:
    data = {
        "email": "uczen@example.test",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Anna",
        "last_name": "Nowak",
        "district": "mazowieckie",
        "birth_year": 2008,
        "grade": 2,
        "phone": "600 100 200",
        "school": "LO nr 1",
        "terms_consent": True,
        "gdpr_consent": True,
        "guardian_consent": True,
    }
    data.update(overrides)
    return data


# --- flaga wyłączona -------------------------------------------------------------------------


def test_the_identifier_is_ignored_without_the_flag(competition, open_registration):
    """Konkurs #1: przysłany identyfikator nie zmienia niczego i nie zakłada dowiązania."""
    row = institution(competition)
    participant = register_participant(**kwargs(custom_institution_id=row.pk))
    assert participant.custom_institution_ref_id is None
    assert participant.school == "LO nr 1"


def test_without_the_flag_the_branch_costs_nothing(competition, django_assert_num_queries):
    """Ta gałąź ma **nie** kosztować zapytania, dopóki konkurs jej nie włączył (§ 5.6).

    Mierzymy sam blok „placówka”, a nie całą rejestrację: całość ma kilkanaście zapytań o konto,
    zgody i członkostwo, a pytanie brzmi „czy doszło **jedno** więcej”.
    """
    row = institution(competition)
    profile = registration_profile(competition)
    with django_assert_num_queries(0):
        resolved = _resolve_institution(
            profile,
            {"school": "LO nr 1", "custom_institution_id": row.pk},
            competition=competition,
        )
    assert resolved["custom_institution_ref"] is None
    assert resolved["school"] == "LO nr 1"


# --- flaga włączona --------------------------------------------------------------------------


def test_choosing_from_the_directory_links_the_row_and_copies_the_name(competition, open_registration):
    """``school`` dostaje nazwę z wykazu – tak samo, jak przy wyborze szkoły z rejestru SIO."""
    with_directory(competition)
    row = institution(competition)
    participant = register_participant(
        **kwargs(
            custom_institution_id=row.pk,
            institution_type=InstitutionType.UNIVERSITY,
            school="cokolwiek wpisanego ręcznie",
        )
    )
    assert participant.custom_institution_ref_id == row.pk
    assert participant.school_ref_id is None
    assert participant.school == "UNIWERSYTET TESTOWY"
    assert participant.institution_name == ""


def test_the_participant_row_survives_a_reread(competition, open_registration):
    """Kolumna jest zapisana, a nie tylko ustawiona w pamięci serwisu."""
    with_directory(competition)
    row = institution(competition)
    participant = register_participant(
        **kwargs(custom_institution_id=row.pk, institution_type=InstitutionType.UNIVERSITY)
    )
    assert Participant.objects.get(pk=participant.pk).custom_institution_ref_id == row.pk


def test_a_row_of_another_competition_is_not_found(competition, other_competition, open_registration):
    """Cudzy identyfikator ma być nie do odróżnienia od nieistniejącego."""
    with_directory(competition)
    row = institution(other_competition)
    with pytest.raises(DomainError) as error:
        register_participant(
            **kwargs(custom_institution_id=row.pk, institution_type=InstitutionType.UNIVERSITY)
        )
    assert error.value.machine_code == "CUSTOM_INSTITUTION_NOT_FOUND"


def test_an_unknown_identifier_is_refused(competition, open_registration):
    with_directory(competition)
    with pytest.raises(DomainError) as error:
        register_participant(
            **kwargs(custom_institution_id=999999, institution_type=InstitutionType.UNIVERSITY)
        )
    assert error.value.machine_code == "CUSTOM_INSTITUTION_NOT_FOUND"


def test_a_deactivated_row_does_not_take_new_registrations(competition, open_registration):
    """Wygaszona placówka zostaje przy profilach sprzed wygaszenia, ale nowego nie przyjmuje."""
    with_directory(competition)
    row = institution(competition, is_active=False)
    with pytest.raises(DomainError) as error:
        register_participant(
            **kwargs(custom_institution_id=row.pk, institution_type=InstitutionType.UNIVERSITY)
        )
    assert error.value.machine_code == "CUSTOM_INSTITUTION_NOT_FOUND"


def test_a_type_outside_the_profile_is_refused_with_the_same_sentence(competition, open_registration):
    """Ten sam komunikat, co przy wykazie publicznym – odmowa nie zależy od wybranej listy."""
    with_directory(competition)
    row = institution(competition, institution_type=InstitutionType.OTHER)
    with pytest.raises(DomainError) as error:
        register_participant(
            **kwargs(custom_institution_id=row.pk, institution_type=InstitutionType.UNIVERSITY)
        )
    assert error.value.machine_code == "INSTITUTION_TYPE_NOT_ALLOWED"


def test_the_public_register_still_works_with_the_flag_on(competition, open_registration):
    """Włączenie słownika własnego nie zamyka drogi przez wykaz publiczny ani wolny tekst."""
    with_directory(competition, allowed_institution_types=[InstitutionType.SECONDARY])
    participant = register_participant(**kwargs(institution_type=InstitutionType.SECONDARY))
    assert participant.custom_institution_ref_id is None
    assert participant.school == "LO nr 1"
