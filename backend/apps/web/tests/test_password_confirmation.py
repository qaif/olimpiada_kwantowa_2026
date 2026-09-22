"""Powtórzenie hasła w obu formularzach rejestracji.

Dlaczego to w ogóle jest w formularzu, a nie tylko „miła wygoda”: literówka w haśle przy rejestracji
jest w praktyce nieodwracalna. Konto powstaje z hasłem, którego nikt nie zna, a jedyna droga odzysku
(„Nie pamiętasz hasła?”) wymaga adresu e-mail, którego uczestnik jeszcze nie potwierdził – więc
zostaje mu czekanie, aż konto skosi kosiarka nieaktywowanych kont, i rejestracja od nowa.

Pole ``password2`` **nie jest regułą domenową** i dlatego nie ma go w serializerach API: klient
programistyczny nie ma literówek w haśle, bo hasła nie przepisuje z klawiatury. Musi za to zniknąć
z ``cleaned_data``, bo widoki wołają serwisy przez ``**form.cleaned_data``.
"""

import pytest

from apps.accounts.models import User
from apps.accounts.tests.factories import InvitationCodeFactory
from apps.web.forms import PASSWORD_MISMATCH_MESSAGE

from .conftest import WEB_TEST_PASSWORD, captcha_fields, participant_extra_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
REGISTER_COMMITTEE_URL = "/register/committee/"


def participant_payload(**overrides) -> dict:
    data = {
        "email": "powtorzenie@example.test",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_date": "1990-12-31",
        "terms_consent": "on",
        "gdpr_consent": "on",
        **participant_extra_fields(),
    }
    data.update(overrides)
    return data


def committee_payload(**overrides) -> dict:
    data = {
        "email": "komitet@example.test",
        "password": WEB_TEST_PASSWORD,
        "password2": WEB_TEST_PASSWORD,
        "first_name": "Jan",
        "last_name": "Kowalski",
        "invitation_code": "kod-testowy-0001",
        **captcha_fields(),
    }
    data.update(overrides)
    return data


def test_matching_passwords_create_the_account(web_client, edition):
    response = web_client.post(REGISTER_URL, participant_payload())

    assert response.status_code == 302
    assert User.objects.filter(email="powtorzenie@example.test").exists()


def test_a_mismatch_shows_the_error_under_the_second_field_and_creates_nothing(web_client, edition):
    response = web_client.post(REGISTER_URL, participant_payload(password2="Inne-Haslo-2026"))

    assert response.status_code == 200
    form = response.context["form"]
    # Błąd stoi **pod polem powtórzenia**, a nie nad formularzem: to jedyne pole, które trzeba
    # poprawić, i jedyne, do którego użytkownik ma wrócić.
    assert form.errors["password2"] == [PASSWORD_MISMATCH_MESSAGE]
    assert not User.objects.filter(email="powtorzenie@example.test").exists()


def test_a_weak_password_lands_under_the_password_field(web_client, edition):
    """``AUTH_PASSWORD_VALIDATORS`` to jedno źródło reguły – formularz tylko pokazuje jej wynik."""
    response = web_client.post(REGISTER_URL, participant_payload(password="krotkie", password2="krotkie"))

    assert response.status_code == 200
    assert response.context["form"].errors["password"]
    assert not User.objects.exists()


def test_the_confirmation_never_reaches_the_service(web_client, edition):
    """``cleaned_data`` jedzie do serwisu jako ``**kwargs`` – nadmiarowy klucz to TypeError."""
    from apps.web.forms import ParticipantRegisterForm

    form = ParticipantRegisterForm(data=participant_payload())

    assert form.is_valid(), form.errors
    assert "password2" not in form.cleaned_data
    assert form.cleaned_data["password"] == WEB_TEST_PASSWORD


def test_the_committee_form_confirms_the_password_too(web_client):
    InvitationCodeFactory(plain_code="kod-testowy-0001")

    refused = web_client.post(REGISTER_COMMITTEE_URL, committee_payload(password2="Inne-Haslo-2026"))

    assert refused.status_code == 200
    assert refused.context["form"].errors["password2"] == [PASSWORD_MISMATCH_MESSAGE]
    assert not User.objects.filter(email="komitet@example.test").exists()
    # Nieudany formularz nie zużywa kodu zaproszenia – sprawdzenie haseł stoi przed serwisem.
    assert web_client.post(REGISTER_COMMITTEE_URL, committee_payload()).status_code == 302
    assert User.objects.filter(email="komitet@example.test").exists()


def test_both_password_inputs_ask_the_browser_for_a_new_password(web_client, edition):
    """Bez ``new-password`` przeglądarka podstawia zapisane hasło do innego konta w tym serwisie."""
    body = web_client.get(REGISTER_URL).content.decode()

    assert body.count('autocomplete="new-password"') >= 2
    assert 'name="password2"' in body


def test_the_api_keeps_a_single_password_field():
    """Powtórzenie jest udogodnieniem interfejsu, a nie regułą domeny – API go nie zna."""
    from apps.accounts.serializers import ParticipantRegisterSerializer

    assert "password2" not in ParticipantRegisterSerializer().fields
