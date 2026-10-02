"""T-02, kryteria 7-8: `GET me/`, logowanie tokenem i wylogowanie."""

import json
from urllib.parse import urlencode

import pytest
from django.test import Client
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import GROUP_PARTICIPANT, GROUP_REVIEWER
from apps.accounts.services import register_participant

from .factories import DEFAULT_PASSWORD, ActiveReviewerFactory, ParticipantFactory, activate

ME_URL = "/api/auth/me/"
LOGIN_URL = "/api/auth/login/"
LOGOUT_URL = "/api/auth/logout/"


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_kryterium_7_me_zwraca_role_i_profil_uczestnika(api):
    """7. GET me/ zwraca role i profil uczestnika."""
    participant = ParticipantFactory()
    api.force_authenticate(user=participant.user)

    resp = api.get(ME_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == participant.user.id
    assert body["email"] == participant.user.email
    assert body["roles"] == [GROUP_PARTICIPANT]
    assert body["participant"]["public_code"] == participant.public_code
    assert body["committee"] is None
    assert "password" not in body


@pytest.mark.django_db
def test_kryterium_7_me_zwraca_profil_komitetu_dla_recenzenta(api):
    """7. GET me/ zwraca profil komitetu i rolę recenzenta."""
    reviewer = ActiveReviewerFactory()
    api.force_authenticate(user=reviewer.user)

    body = api.get(ME_URL).json()

    assert body["roles"] == [GROUP_REVIEWER]
    assert body["participant"] is None
    assert body["committee"]["status"] == "ACTIVE"


@pytest.mark.django_db
def test_kryterium_7_me_dla_niezalogowanego_zwraca_401(api):
    """7. Niezalogowany → 401."""
    resp = api.get(ME_URL)

    assert resp.status_code == 401


@pytest.mark.django_db
def test_kryterium_8_login_zwraca_token_dzialajacy_w_naglowku_authorization(api):
    """8. Login zwraca token; token działa w nagłówku `Authorization: Token ...`."""
    participant = ParticipantFactory()

    resp = api.post(LOGIN_URL, {"email": participant.user.email, "password": DEFAULT_PASSWORD}, format="json")

    assert resp.status_code == 200, resp.data
    token = resp.json()["token"]
    assert token == Token.objects.get(user=participant.user).key
    assert DEFAULT_PASSWORD not in resp.content.decode()

    # świeży klient bez sesji – uwierzytelnia wyłącznie nagłówek z tokenem
    token_client = APIClient()
    assert token_client.get(ME_URL).status_code == 401
    token_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    me = token_client.get(ME_URL)
    assert me.status_code == 200
    assert me.json()["email"] == participant.user.email


@pytest.mark.django_db
def test_kryterium_8_login_dziala_niezaleznie_od_wielkosci_liter_w_emailu(api, open_registration):
    """8. E-mail jest identyfikatorem bez rozróżniania wielkości liter (normalizacja przy rejestracji)."""
    registered = register_participant(
        email="Wielkie@Example.Test",
        password=DEFAULT_PASSWORD,
        first_name="Ewa",
        last_name="Zielińska",
        school="LO nr 2",
        district="mazowieckie",
        grade=3,
        birth_year=2007,
        phone="600 100 200",
        terms_consent=True,
        gdpr_consent=True,
    )
    assert registered.user.email == "wielkie@example.test"
    # Konto z rejestracji czeka na link aktywacyjny – tu przedmiotem testu jest normalizacja
    # adresu przy logowaniu, więc aktywację przechodzimy helperem (tą samą drogą, co kliknięcie
    # linku z listu).
    activate(registered.user)

    resp = api.post(LOGIN_URL, {"email": "WIELKIE@example.test", "password": DEFAULT_PASSWORD}, format="json")

    assert resp.status_code == 200, resp.data


@pytest.mark.django_db
def test_login_ze_zlym_haslem_zwraca_400_invalid_credentials(api):
    """Złe hasło nie ujawnia, czy konto istnieje, i nie tworzy tokenu."""
    participant = ParticipantFactory()

    resp = api.post(LOGIN_URL, {"email": participant.user.email, "password": "zle-haslo"}, format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_CREDENTIALS"
    assert not Token.objects.filter(user=participant.user).exists()


@pytest.mark.django_db
def test_logout_usuwa_token_i_konczy_sesje(api):
    """Po wylogowaniu token przestaje działać."""
    participant = ParticipantFactory()
    token = api.post(
        LOGIN_URL, {"email": participant.user.email, "password": DEFAULT_PASSWORD}, format="json"
    ).json()["token"]

    token_client = APIClient()
    token_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert token_client.post(LOGOUT_URL).status_code == 204
    assert not Token.objects.filter(user=participant.user).exists()
    assert token_client.get(ME_URL).status_code == 401


@pytest.mark.django_db
def test_logout_dla_niezalogowanego_zwraca_401(api):
    assert api.post(LOGOUT_URL).status_code == 401


# --- logowanie wyłącznie JSON-em (pakiet 5 po audycie: login CSRF) --------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["form", "multipart"])
def test_login_refuses_a_body_that_a_cross_site_form_can_send(fmt):
    """Formularz z obcej strony wysyła ``x-www-form-urlencoded``/``multipart`` bez preflightu.

    Takie logowanie (widok DRF bez CSRF + ``auth.login``) zalogowałoby przeglądarkę ofiary na
    konto napastnika. Odpowiedź ``415`` zapada, zanim hasło zostanie sprawdzone – nie ma tokenu
    ani ciasteczka sesji.
    """
    participant = ParticipantFactory()
    client = Client(enforce_csrf_checks=True)
    data = {"email": participant.user.email, "password": DEFAULT_PASSWORD}
    if fmt == "form":
        resp = client.post(LOGIN_URL, urlencode(data), content_type="application/x-www-form-urlencoded")
    else:
        resp = client.post(LOGIN_URL, data)

    assert resp.status_code == 415
    assert "sessionid" not in resp.cookies
    assert not Token.objects.filter(user=participant.user).exists()


@pytest.mark.django_db
def test_login_with_json_still_works_and_text_plain_does_not(api):
    """``text/plain`` to trzeci typ, który formularz HTML umie wysłać bez preflightu."""
    participant = ParticipantFactory()
    body = {"email": participant.user.email, "password": DEFAULT_PASSWORD}

    as_text = api.post(LOGIN_URL, json.dumps(body), content_type="text/plain")
    assert as_text.status_code == 415

    assert api.post(LOGIN_URL, body, format="json").status_code == 200
