"""Aktywacja konta w interfejsie WWW: link z listu, ponowna wysyłka, obejście koordynatora.

Trzy rzeczy, których pilnują te testy i które łatwo zepsuć przy pierwszej „poprawce wygody”:

- **formularz logowania nie mówi, że konto jest nieaktywne.** ``ModelBackend`` odrzuca je tym samym,
  ogólnym komunikatem co złe hasło – i tak ma zostać, bo komunikat „konto nieaktywne” potwierdzałby
  obcemu, że pod tym adresem konto istnieje,
- **publiczny formularz ponownej wysyłki nie zdradza, kto ma konto.** Ta sama odpowiedź dla adresu
  istniejącego i nieistniejącego, limit konsumowany przez **każdy** POST,
- **koordynator może aktywować ręcznie**, dopóki dostarczalność poczty nie jest pewna – i zostawia
  po tym inny wpis audytowy niż kliknięcie linku, bo adres został potwierdzony czym innym.
"""

import re

import pytest
from django.conf import settings
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.accounts.activation import RESEND_MESSAGE, make_activation_token
from apps.accounts.models import User
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory
from apps.core.models import AuditLog

from .conftest import participant_extra_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
RESEND_URL = "/activate/resend/"
LOGIN_URL = "/login/"


def register_payload(**overrides) -> dict:
    data = {
        "email": "aktywacja-web@example.test",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 1990,
        "terms_consent": "on",
        "gdpr_consent": "on",
        **participant_extra_fields(),
    }
    data.update(overrides)
    return data


def rest_framework_with(**rates) -> dict:
    """Kopia ``REST_FRAMEWORK`` z podmienionymi stawkami – tak samo jak w test_password_reset.py."""
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def waiting_user(**overrides) -> User:
    """Konto, które czeka na potwierdzenie adresu – tak jak zaraz po rejestracji."""
    return UserFactory(is_active=False, email_verified_at=None, **overrides)


def link_from(message) -> str:
    match = re.search(r"https?://\S*/activate/\S+", message.body)
    assert match, message.body
    return match.group(0)


# --- rejestracja → list → aktywacja -------------------------------------------------------------


def test_registration_tells_the_user_to_check_the_mailbox(
    web_client, edition, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(REGISTER_URL, register_payload(), follow=True)

    body = response.content.decode()
    assert "Sprawdź skrzynkę e-mail" in body
    assert "link aktywacyjny" in body
    # Komunikat musi mówić o czterech godzinach: po tym czasie konto znika i rejestrację
    # trzeba powtórzyć.
    assert "24 godziny" in body


def test_the_link_from_the_message_activates_the_account(
    web_client, edition, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(REGISTER_URL, register_payload())

    response = web_client.get(link_from(mail.outbox[0]))

    assert response.status_code == 200
    assert "Konto aktywne" in response.content.decode()
    user = User.objects.get(email="aktywacja-web@example.test")
    assert user.is_active is True
    # Aktywacja **nie loguje**: dostęp do skrzynki nie zamienia się jednym kliknięciem w sesję.
    assert "_auth_user_id" not in web_client.session


def test_a_broken_link_shows_the_resend_form(web_client):
    response = web_client.get("/activate/podrobiony-token/")

    assert response.status_code == 400
    body = response.content.decode()
    assert "Link aktywacyjny nie działa" in body
    assert RESEND_URL in body


def test_an_activated_account_can_log_in_and_an_inactive_one_cannot(web_client):
    user = waiting_user(email="czeka@example.test", password=DEFAULT_PASSWORD)

    refused = web_client.post(LOGIN_URL, {"username": user.email, "password": DEFAULT_PASSWORD})

    assert refused.status_code == 200
    body = refused.content.decode()
    # Ogólny komunikat Django – ani słowa o tym, że konto istnieje i czeka na aktywację.
    assert "nieaktywne" not in body.lower()
    assert "_auth_user_id" not in web_client.session

    web_client.get(f"/activate/{make_activation_token(user)}/")
    accepted = web_client.post(LOGIN_URL, {"username": user.email, "password": DEFAULT_PASSWORD})
    assert accepted.status_code == 302


def test_login_page_always_offers_the_resend_link(web_client):
    """Podpowiedź jest stała, bo warunkowa byłaby informacją o istnieniu konta."""
    body = web_client.get(LOGIN_URL).content.decode()

    assert "Nie dostałeś linku aktywacyjnego?" in body
    assert f'href="{RESEND_URL}"' in body


# --- ponowna wysyłka: brak enumeracji i limit ---------------------------------------------------


def test_resend_answers_identically_for_a_known_and_an_unknown_address(
    web_client, django_capture_on_commit_callbacks
):
    waiting_user(email="istnieje@example.test")

    # Dwa osobne bloki, bo ``django_capture_on_commit_callbacks`` wykonuje zebrane wywołania
    # dopiero na wyjściu – w jednym bloku nie dałoby się policzyć listów po pierwszym POST-cie.
    with django_capture_on_commit_callbacks(execute=True):
        known = web_client.post(RESEND_URL, {"email": "istnieje@example.test"}, follow=True)
    sent_for_known = len(mail.outbox)
    with django_capture_on_commit_callbacks(execute=True):
        unknown = web_client.post(RESEND_URL, {"email": "nie-istnieje@example.test"}, follow=True)

    assert known.status_code == unknown.status_code == 200
    assert RESEND_MESSAGE in known.content.decode()
    assert RESEND_MESSAGE in unknown.content.decode()
    # List poszedł tylko dla konta, które faktycznie czeka – ale odpowiedź tego nie wydaje.
    assert sent_for_known == 1
    assert len(mail.outbox) == 1


@override_settings(REST_FRAMEWORK=rest_framework_with(password_reset="2/hour"))
def test_resend_is_throttled_with_the_password_reset_budget(web_client):
    """Wspólny scope: oba formularze wysyłają list na adres podany przez anonima."""
    waiting_user(email="limit@example.test")

    for attempt in range(2):
        assert web_client.post(RESEND_URL, {"email": "limit@example.test"}).status_code == 302, attempt

    blocked = web_client.post(RESEND_URL, {"email": "limit@example.test"})
    assert blocked.status_code == 429


# --- obejście koordynatora ----------------------------------------------------------------------


def test_coordinator_sees_the_waiting_accounts_with_the_time_left(web_client, coordinator):
    waiting_user(email="czeka@example.test", groups=["participant"])
    web_client.force_login(coordinator)

    body = web_client.get(reverse("web:coordinator-activations")).content.decode()

    assert "Konta oczekujące na aktywację" in body
    assert "czeka@example.test" in body
    assert "participant" in body
    # Bez pozostałego czasu przycisk „Aktywuj ręcznie” byłby ruletką: konto po czasie zniknie
    # przy najbliższym przebiegu kosiarki.
    assert "min" in body


def test_coordinator_activates_an_account_manually_and_it_is_audited(web_client, coordinator):
    user = waiting_user(email="recznie@example.test")
    web_client.force_login(coordinator)

    response = web_client.post(reverse("web:coordinator-account-activate", args=[user.pk]))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.is_active is True
    assert user.email_verified_at is not None
    entry = AuditLog.objects.get(action="account.activated_by_coordinator")
    assert entry.actor == coordinator
    assert str(user.pk) == entry.target_id
    # W audycie nie ma adresu e-mail – kogo dotyczy wpis, mówi ``target_id``.
    assert "recznie@example.test" not in str(entry.diff)


def test_coordinator_can_resend_the_link(web_client, coordinator, django_capture_on_commit_callbacks):
    user = waiting_user(email="ponownie@example.test")
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(reverse("web:coordinator-account-resend", args=[user.pk]))

    assert response.status_code == 302
    assert [message.to for message in mail.outbox] == [["ponownie@example.test"]]


def test_activating_an_already_active_account_is_refused(web_client, coordinator):
    active = UserFactory(email="juz-aktywne@example.test")
    web_client.force_login(coordinator)

    response = web_client.post(reverse("web:coordinator-account-activate", args=[active.pk]), follow=True)

    assert "już aktywne" in response.content.decode()


def test_the_waiting_list_is_closed_to_everyone_but_the_coordinator(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(reverse("web:coordinator")).status_code == 403
    assert (
        web_client.post(reverse("web:coordinator-account-activate", args=[participant.user.pk])).status_code
        == 403
    )
