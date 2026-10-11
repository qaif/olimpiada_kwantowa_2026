"""Zmiana adresu e-mail i wyłączenie 2FA wymagają hasła; zmiana adresu kończy inne sesje (S11).

Scenariusz z audytu 10.10.2026: przejęta albo porzucona sesja → nowy adres napastnika →
potwierdzenie na **jego** skrzynce → „Nie pamiętasz hasła?”. Dlatego wniosek o zmianę adresu
i wyłączenie drugiego składnika wymagają aktualnego hasła, a potwierdzenie nowego adresu kasuje
token API i sesje w innych przeglądarkach. Sesja, w której kliknięto link, zostaje – to ona
dowiodła dostępu do nowej skrzynki.

Po scaleniu z main obowiązuje mocniejsza reguła AUTH-01b/SEC-01 (``apps.accounts.reauth``): pole
``current_password``, konto bez hasła (Google/Facebook) nie przepisuje adresu, tylko ustawia hasło
linkiem na obecny adres, a wyłączenie 2FA żąda hasła **i** bieżącego kodu.
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
    wrong = web_client.post(EMAIL_URL, {"new_email": "nowy@example.test", "current_password": "zle-haslo"})

    assert without.status_code == 200
    assert wrong.status_code == 200
    assert "Aktualne hasło jest nieprawidłowe." in wrong.content.decode()
    assert mail.outbox == []


def test_the_email_change_form_asks_for_the_password(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get("/me/profile/").content.decode()

    assert 'name="current_password"' in body


def test_an_account_without_a_password_must_set_one_first(web_client, django_capture_on_commit_callbacks):
    """Konto Google/Facebooka nie przenosi adresu przepisaniem obecnego – ustawia najpierw hasło."""
    user = UserFactory(email="google@example.test")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    web_client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        refused = web_client.post(
            EMAIL_URL, {"new_email": "nowy@example.test", "current_email": "google@example.test"}
        )

    assert refused.status_code == 200
    assert "nie ma jeszcze hasła" in refused.content.decode()
    assert mail.outbox == []


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
        web_client.post(EMAIL_URL, {"new_email": "nowy@example.test", "current_password": DEFAULT_PASSWORD})
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
    # Konto i niewykorzystane kody zapasowe – od SEC-01 wyłączenie żąda też bieżącego kodu.
    return participant.user, codes[1:]


def test_disabling_2fa_needs_the_current_password(web_client, with_2fa):
    user, codes = with_2fa
    web_client.post(DISABLE_URL)
    web_client.post(DISABLE_URL, {"password": "zle-haslo", "code": codes[0]})

    assert twofactor.device_for(user) is not None

    web_client.post(DISABLE_URL, {"password": DEFAULT_PASSWORD, "code": codes[1]})

    assert twofactor.device_for(user) is None


def test_the_2fa_screen_asks_for_the_password(web_client, with_2fa):
    body = web_client.get("/account/2fa/").content.decode()

    assert 'name="password"' in body
