"""Telefon uczestnika: normalizacja zapisu i wymagalność na każdej drodze rejestracji.

Sedno tych testów: dwa zapisy tego samego numeru muszą dać w bazie **jeden** napis. Bez tego
koordynator szukający numeru po fragmencie nie znajduje połowy uczestników, a ten, kto wpisał
numer z nawiasami, jest w bazie kimś innym niż ten, kto wpisał go ze spacjami.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Participant
from apps.accounts.phones import MAX_DIGITS, normalize_phone
from apps.accounts.services import register_participant
from apps.core.api import DomainError

REGISTER_URL = "/api/auth/register/participant/"
PASSWORD = "Poprawne-Haslo-2026"


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        # Dziewięć cyfr bez prefiksu = numer krajowy; prefiks dokładamy za uczestnika.
        ("600100200", "+48600100200"),
        ("600 100 200", "+48600100200"),
        ("600-100-200", "+48600100200"),
        ("(600) 100 200", "+48600100200"),
        # Prefiks podany jawnie – zostaje taki, jaki jest.
        ("+48 600 100 200", "+48600100200"),
        ("+48600100200", "+48600100200"),
        # „00” to międzynarodowy prefiks wybierania: ten sam numer, inny zapis.
        ("0048 600 100 200", "+48600100200"),
        # Numer zagraniczny zostaje ze swoim prefiksem – nie podmieniamy go na polski.
        ("+49 30 123456", "+4930123456"),
        # Zapis krajowy z zerem wiodącym nie ma dziewięciu cyfr, więc prefiksu nie zgadujemy.
        ("0600100200", "0600100200"),
    ],
)
def test_the_same_number_written_differently_lands_as_one_string(written, stored):
    assert normalize_phone(written) == stored


@pytest.mark.parametrize(
    "written",
    [
        "",  # puste przy wymaganym numerze
        "brak",  # litery
        "600 100",  # sześć cyfr – numer skrócony, nie da się zadzwonić z zewnątrz
        "1" * (MAX_DIGITS + 1),  # ponad górny limit
        "600+100200",  # plus w środku to literówka, nie prefiks
        "600/100/200",  # ukośnik nie jest separatorem, którego używamy
    ],
)
def test_unusable_numbers_are_refused(written):
    with pytest.raises(DomainError) as exc:
        normalize_phone(written)

    assert exc.value.machine_code in {"PHONE_INVALID", "PHONE_REQUIRED"}


def test_blank_is_allowed_only_when_the_caller_says_so():
    """Profile sprzed wprowadzenia pola nie mają numeru i nie wolno ich unieważnić."""
    assert normalize_phone("", required=False) == ""
    assert normalize_phone(None, required=False) == ""


@pytest.mark.django_db
def test_service_stores_the_normalised_number(open_registration):
    participant = register_participant(
        email="telefon@example.test",
        password=PASSWORD,
        first_name="Anna",
        last_name="Nowak",
        school="LO nr 1",
        district="mazowieckie",
        grade=2,
        birth_year=1990,
        phone="(600) 100-200",
        terms_consent=True,
        gdpr_consent=True,
    )

    assert participant.phone == "+48600100200"


@pytest.mark.django_db
def test_api_refuses_a_registration_without_a_phone(open_registration):
    response = APIClient().post(
        REGISTER_URL,
        {
            "email": "bez-telefonu@example.test",
            "password": PASSWORD,
            "first_name": "Anna",
            "last_name": "Nowak",
            "school": "LO nr 1",
            "district": "mazowieckie",
            "grade": 2,
            "birth_year": 1990,
            "terms_consent": True,
            "gdpr_consent": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "phone" in response.json()["detail"]
    assert not Participant.objects.exists()


@pytest.mark.django_db
def test_form_refuses_a_registration_without_a_phone():
    """Ta sama reguła w formularzu WWW – inaczej jedna z dróg wpuszczałaby profil bez numeru."""
    from apps.web.forms import ParticipantRegisterForm

    form = ParticipantRegisterForm(
        data={
            "email": "bez-telefonu@example.test",
            "password": PASSWORD,
            "password2": PASSWORD,
            "first_name": "Anna",
            "last_name": "Nowak",
            "school_custom": "on",
            "school": "LO nr 1",
            "district": "mazowieckie",
            "grade": 2,
            "birth_year": 1990,
            "terms_consent": "on",
            "gdpr_consent": "on",
        }
    )

    assert form.is_valid() is False
    assert "phone" in form.errors
