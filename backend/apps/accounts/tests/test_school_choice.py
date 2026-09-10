"""Wybór szkoły i klasa przy rejestracji uczestnika (serwis + API).

Reguła jest jedna dla wszystkich wejść: albo szkoła ze słownika (``school_id``), albo jej nazwa
wpisana ręcznie (``school``) – i zawsze klasa 1–5.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Participant
from apps.accounts.services import register_participant, register_social_participant
from apps.core.api import DomainError
from apps.schools.tests.factories import SchoolFactory

REGISTER_URL = "/api/auth/register/participant/"
PASSWORD = "Poprawne-Haslo-2026"


def kwargs(**overrides) -> dict:
    data = {
        "email": "uczen@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Nowak",
        "district": "mazowieckie",
        "birth_year": 2008,
        "grade": 2,
        "gdpr_consent": True,
    }
    data.update(overrides)
    return data


@pytest.fixture
def api():
    return APIClient()


# --- serwis -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_from_the_directory_is_copied_by_name_and_linked(open_registration):
    school = SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE", city="Warszawa")

    participant = register_participant(**kwargs(school_id=school.id))

    # Nazwa jest przepisana z rejestru, a nie z tego, co uczestnik miał w polu tekstowym –
    # dopiero to sprawia, że wszyscy uczniowie tej szkoły mają w bazie ten sam napis.
    assert participant.school == "XIV LICEUM OGÓLNOKSZTAŁCĄCE"
    assert participant.school_ref == school
    assert participant.grade == 2


@pytest.mark.django_db
def test_free_text_school_is_accepted_without_a_directory_row(open_registration):
    participant = register_participant(**kwargs(school="Lycée Français de Varsovie"))

    assert participant.school == "Lycée Français de Varsovie"
    assert participant.school_ref is None


@pytest.mark.django_db
def test_unknown_school_id_is_a_domain_error(open_registration):
    with pytest.raises(DomainError) as exc:
        register_participant(**kwargs(school_id=999999))

    assert exc.value.machine_code == "SCHOOL_NOT_FOUND"
    assert exc.value.status_code == 400
    # Konto nie może powstać przed rozstrzygnięciem szkoły – inaczej zostałby po nim sierota.
    assert not Participant.objects.exists()


@pytest.mark.django_db
def test_inactive_school_cannot_be_chosen(open_registration):
    """Wiersz wygaszony przez ``seed_schools`` zostaje w bazie dla starych profili, ale nie do wyboru."""
    school = SchoolFactory(is_active=False)

    with pytest.raises(DomainError) as exc:
        register_participant(**kwargs(school_id=school.id))

    assert exc.value.machine_code == "SCHOOL_NOT_FOUND"


@pytest.mark.django_db
def test_no_school_at_all_is_refused(open_registration):
    with pytest.raises(DomainError) as exc:
        register_participant(**kwargs())

    assert exc.value.machine_code == "SCHOOL_REQUIRED"


@pytest.mark.django_db
def test_too_short_free_text_is_refused(open_registration):
    with pytest.raises(DomainError) as exc:
        register_participant(**kwargs(school="  LO  "))

    assert exc.value.machine_code == "SCHOOL_REQUIRED"


@pytest.mark.django_db
@pytest.mark.parametrize("grade", [None, 0, 6, "trzecia"])
def test_grade_outside_one_to_five_is_refused(open_registration, grade):
    with pytest.raises(DomainError) as exc:
        register_participant(**kwargs(school="LO nr 1", grade=grade))

    assert exc.value.machine_code == "GRADE_INVALID"


@pytest.mark.django_db
def test_social_registration_follows_the_same_rules(open_registration):
    school = SchoolFactory(name="TECHNIKUM ŁĄCZNOŚCI", city="Kraków")

    participant = register_social_participant(
        email="google@example.test",
        first_name="Anna",
        last_name="Nowak",
        district="malopolskie",
        birth_year=2008,
        grade=4,
        gdpr_consent=True,
        school_id=school.id,
    )

    assert participant.school == "TECHNIKUM ŁĄCZNOŚCI"
    assert participant.school_ref == school
    assert participant.grade == 4


@pytest.mark.django_db
def test_social_registration_refuses_a_missing_school(open_registration):
    with pytest.raises(DomainError) as exc:
        register_social_participant(
            email="google@example.test",
            first_name="Anna",
            last_name="Nowak",
            district="malopolskie",
            birth_year=2008,
            grade=4,
            gdpr_consent=True,
        )

    assert exc.value.machine_code == "SCHOOL_REQUIRED"


# --- API --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_accepts_school_id(api, open_registration):
    school = SchoolFactory(name="V LICEUM OGÓLNOKSZTAŁCĄCE", city="Gdańsk")

    response = api.post(
        REGISTER_URL,
        {
            "email": "api@example.test",
            "password": PASSWORD,
            "first_name": "Jan",
            "last_name": "Kowalski",
            "school_id": school.id,
            "district": "pomorskie",
            "grade": 1,
            "birth_year": 2009,
            "gdpr_consent": True,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    participant = Participant.objects.get(user__email="api@example.test")
    assert participant.school_ref == school
    assert participant.school == "V LICEUM OGÓLNOKSZTAŁCĄCE"


@pytest.mark.django_db
def test_api_still_accepts_plain_school_text(api, open_registration):
    """Zgodność wsteczna: klient sprzed słownika zna wyłącznie tekstowe ``school``."""
    response = api.post(
        REGISTER_URL,
        {
            "email": "stary-klient@example.test",
            "password": PASSWORD,
            "first_name": "Jan",
            "last_name": "Kowalski",
            "school": "LO nr 3",
            "district": "pomorskie",
            "grade": 5,
            "birth_year": 2009,
            "gdpr_consent": True,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Participant.objects.get(user__email="stary-klient@example.test").school_ref is None


@pytest.mark.django_db
def test_api_refuses_a_payload_without_any_school(api, open_registration):
    response = api.post(
        REGISTER_URL,
        {
            "email": "bez-szkoly@example.test",
            "password": PASSWORD,
            "first_name": "Jan",
            "last_name": "Kowalski",
            "district": "pomorskie",
            "grade": 1,
            "birth_year": 2009,
            "gdpr_consent": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert not Participant.objects.exists()


@pytest.mark.django_db
def test_api_requires_the_grade(api, open_registration):
    response = api.post(
        REGISTER_URL,
        {
            "email": "bez-klasy@example.test",
            "password": PASSWORD,
            "first_name": "Jan",
            "last_name": "Kowalski",
            "school": "LO nr 3",
            "district": "pomorskie",
            "birth_year": 2009,
            "gdpr_consent": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "grade" in response.data["detail"]
