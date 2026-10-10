"""Zmiana adresu e-mail i wyłączenie 2FA wymagają hasła; zmiana adresu kończy inne sesje (S11).

Scenariusz z audytu 10.10.2026: przejęta albo porzucona sesja → nowy adres napastnika →
potwierdzenie na **jego** skrzynce → „Nie pamiętasz hasła?”. Dlatego wniosek o zmianę adresu
i wyłączenie drugiego składnika potwierdza się tak samo, jak usunięcie konta (hasło, a konto bez
hasła – przepisanie obecnego adresu), a potwierdzenie nowego adresu kasuje token API i sesje
w innych przeglądarkach. Sesja, w której kliknięto link, zostaje – to ona dowiodła dostępu do
nowej skrzynki.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.test import Client

from apps.accounts import twofactor
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db

EMAIL_URL = "/account/email/"
DISABLE_URL = "/account/2fa/disable/"


def confirmation_path(message) -> str:
    match = re.search(r"(?:https?://[^/\s]+)?(/account/email/confirm/\S+)", message.body)
    assert match, message.body
    return match.group(1)


def test_the_email_change_needs_the_current_password(web_client, participant):
    web_client.force_login(participant.user)

    without = web_client.post(EMAIL_URL, {"new_email": "nowy@example.test"})
    wrong = web_client.post(EMAIL_URL, {"new_email": "nowy@example.test", "password": "zle-haslo"})

    assert without.status_code == 200
    assert wrong.status_code == 200
    assert "Nieprawidłowe hasło." in wrong.content.decode()
    assert mail.outbox == []


def test_the_email_change_form_asks_for_the_password(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get("/me/profile/").content.decode()

    assert 'name="password"' in body
    assert 'name="current_email"' not in body


def test_an_account_without_a_password_retypes_its_address(web_client, django_capture_on_commit_callbacks):
    user = UserFactory(email="google@example.test")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    web_client.force_login(user)

    refused = web_client.post(
        EMAIL_URL, {"new_email": "nowy@example.test", "current_email": "inny@example.test"}
    )
    assert refused.status_code == 200
    assert mail.outbox == []

    with django_capture_on_commit_callbacks(execute=True):
        accepted = web_client.post(
            EMAIL_URL, {"new_email": "nowy@example.test", "current_email": "google@example.test"}
        )

    assert accepted.status_code == 302
    assert [message.to for message in mail.outbox] == [["nowy@example.test"]]


def test_confirming_the_new_address_ends_other_sessions_and_the_api_token(
    web_client, participant, django_capture_on_commit_callbacks
):
    from rest_framework.authtoken.models import Token

    user = participant.user
    Token.objects.create(user=user)
    elsewhere = Client()
    elsewhere.force_login(user)
    assert elsewhere.get("/me/profile/").status_code == 200

    web_client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(EMAIL_URL, {"new_email": "nowy@example.test", "password": DEFAULT_PASSWORD})
    link = confirmation_path(next(m for m in mail.outbox if m.to == ["nowy@example.test"]))
    with django_capture_on_commit_callbacks(execute=True):
        assert web_client.get(link).status_code == 200

    user.refresh_from_db()
    assert user.email == "nowy@example.test"
    assert not Token.objects.filter(user=user).exists()
    # Inna przeglądarka – wylogowana; ta, w której kliknięto link – dalej zalogowana.
    assert elsewhere.get("/me/profile/").status_code == 302
    assert web_client.get("/me/profile/").status_code == 200


@pytest.fixture
def with_2fa(settings, participant, web_client):
    settings.TWO_FACTOR_ENABLED = True
    device = twofactor.begin_setup(participant.user)
    code = twofactor.totp_code(device.plain_secret(), twofactor.current_counter())
    codes = twofactor.confirm_setup(participant.user, code)
    web_client.force_login(participant.user)
    web_client.post("/login/2fa/", {"code": codes[0]})
    return participant.user


def test_disabling_2fa_needs_the_current_password(web_client, with_2fa):
    web_client.post(DISABLE_URL)
    web_client.post(DISABLE_URL, {"password": "zle-haslo"})

    assert twofactor.device_for(with_2fa) is not None

    web_client.post(DISABLE_URL, {"password": DEFAULT_PASSWORD})

    assert twofactor.device_for(with_2fa) is None


def test_the_2fa_screen_asks_for_the_password(web_client, with_2fa):
    body = web_client.get("/account/2fa/").content.decode()

    assert 'name="password"' in body
