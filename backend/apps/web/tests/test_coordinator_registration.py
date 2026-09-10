"""Rejestracja uczestników sterowana przez koordynatora – ekran panelu i skutki na wszystkich drogach.

Cztery rzeczy, na których to stoi:

- ekran ``/coordinator/registration/`` zapisuje okno i zostawia ślad w audycie,
- odwrócone okno nie zapisuje niczego, a komunikat staje pod polem,
- zamknięta rejestracja odmawia **tak samo** formularzowi, API i logowaniu społecznościowemu,
- strona główna i menu pokazują zapowiedź startu zamiast przycisku, którego kliknięcie
  skończyłoby się odmową.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from django.utils import timezone

from apps.accounts.models import Participant, User
from apps.competitions.models import Edition
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

REGISTRATION_URL = "/coordinator/registration/"
REGISTER_URL = "/register/"
API_REGISTER_URL = "/api/auth/register/participant/"
WARSAW_FORMAT = "%Y-%m-%dT%H:%M"


def form_data(**overrides) -> dict:
    """Komplet pól ``RegistrationSettingsForm``. Puste pole daty = „bez ograniczenia”."""
    data = {"registration_enabled": "on", "registration_opens_at": "", "registration_closes_at": ""}
    data.update(overrides)
    return {name: value for name, value in data.items() if value is not None}


def local(value) -> str:
    return timezone.localtime(value).strftime(WARSAW_FORMAT)


def registration_payload(email: str = "nowy@example.test") -> dict:
    return {
        "email": email,
        "password": "Poprawne-Haslo-2026",
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 3",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 2008,
        "gdpr_consent": True,
        "guardian_consent": True,
    }


def web_form_payload(email: str = "nowy@example.test") -> dict:
    """To samo, co payload API, w kształcie formularza HTML (pola wyboru jako ``on``)."""
    payload = registration_payload(email)
    payload["gdpr_consent"] = "on"
    payload["guardian_consent"] = "on"
    return payload


# --- panel koordynatora ---------------------------------------------------------------------------


def test_form_shows_the_current_window(web_client, coordinator, edition):
    opens_at = timezone.now() + timedelta(days=3)
    Edition.objects.filter(pk=edition.pk).update(registration_opens_at=opens_at)
    web_client.force_login(coordinator)

    response = web_client.get(REGISTRATION_URL)

    assert response.status_code == 200
    assert local(opens_at) in response.content.decode()


def test_saving_the_window_writes_audit_and_returns_to_the_dashboard(web_client, coordinator, edition):
    web_client.force_login(coordinator)
    opens_at = timezone.localtime(timezone.now()) + timedelta(days=7)

    response = web_client.post(REGISTRATION_URL, form_data(registration_opens_at=local(opens_at)))

    edition.refresh_from_db()
    assert response.status_code == 302
    assert response.headers["Location"] == "/coordinator/"
    assert edition.registration_opens_at == opens_at.replace(second=0, microsecond=0)
    entry = AuditLog.objects.get(action="edition.registration_updated")
    assert entry.actor == coordinator
    assert "registration_opens_at" in entry.diff


def test_switching_registration_off_writes_audit_and_closes_it(web_client, coordinator, edition):
    web_client.force_login(coordinator)

    web_client.post(REGISTRATION_URL, form_data(registration_enabled=None))

    edition.refresh_from_db()
    assert edition.registration_enabled is False
    assert edition.registration_status().reason == "disabled"
    assert AuditLog.objects.filter(action="edition.registration_updated").count() == 1


def test_reversed_window_is_rejected_under_the_field(web_client, coordinator, edition):
    web_client.force_login(coordinator)
    opens_at = timezone.localtime(timezone.now()) + timedelta(days=7)

    response = web_client.post(
        REGISTRATION_URL,
        form_data(
            registration_opens_at=local(opens_at),
            registration_closes_at=local(opens_at - timedelta(days=1)),
        ),
    )

    edition.refresh_from_db()
    assert response.status_code == 400
    assert "Zamknięcie rejestracji musi być po jej otwarciu." in response.content.decode()
    assert edition.registration_opens_at is None
    assert not AuditLog.objects.filter(action="edition.registration_updated").exists()


def test_equal_moments_are_rejected_too(web_client, coordinator, edition):
    """„Otwarcie = zamknięcie” to okno o zerowej długości, czyli rejestracja, która nigdy nie trwa."""
    web_client.force_login(coordinator)
    moment = local(timezone.localtime(timezone.now()) + timedelta(days=7))

    response = web_client.post(
        REGISTRATION_URL, form_data(registration_opens_at=moment, registration_closes_at=moment)
    )

    edition.refresh_from_db()
    assert response.status_code == 400
    assert edition.registration_closes_at is None


def test_dashboard_shows_the_state_and_links_to_the_settings(web_client, coordinator, edition):
    Edition.objects.filter(pk=edition.pk).update(registration_enabled=False)
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "Rejestracja uczestników:" in content
    assert "wyłączona" in content
    assert f'href="{REGISTRATION_URL}"' in content


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_open_the_settings(web_client, participant, reviewer, edition, role):
    web_client.force_login(participant.user if role == "participant" else reviewer.user)

    assert web_client.get(REGISTRATION_URL).status_code == 403
    assert web_client.post(REGISTRATION_URL, form_data()).status_code == 403


def test_anonymous_is_redirected_to_login(web_client, edition):
    response = web_client.get(REGISTRATION_URL)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


def test_without_a_current_edition_the_screen_explains_itself(web_client, coordinator):
    web_client.force_login(coordinator)

    response = web_client.get(REGISTRATION_URL, follow=True)

    assert "Nie ustawiono bieżącej edycji" in response.content.decode()


# --- formularz /register/ --------------------------------------------------------------------------


def test_register_page_shows_the_form_when_open(web_client, edition):
    content = web_client.get(REGISTER_URL).content.decode()

    assert "Rejestracja jest otwarta." in content
    assert 'name="password"' in content


def test_register_page_shows_the_announcement_instead_of_the_form(web_client, edition):
    opens_at = timezone.make_aware(timezone.datetime(2027, 9, 8, 0, 0))
    Edition.objects.filter(pk=edition.pk).update(registration_opens_at=opens_at)

    content = web_client.get(REGISTER_URL).content.decode()

    assert "Rejestracja rusza 8 września 2027 o 00:00." in content
    assert 'name="password"' not in content


def test_post_to_a_closed_registration_creates_nothing(web_client, edition):
    """Ukrycie formularza jest uprzejmością – regułą jest bramka w serwisie, także dla POST-a."""
    Edition.objects.filter(pk=edition.pk).update(registration_enabled=False)

    response = web_client.post(REGISTER_URL, web_form_payload())

    assert response.status_code == 200
    assert "Rejestracja uczestników jest obecnie wyłączona." in response.content.decode()
    assert not User.objects.filter(email="nowy@example.test").exists()
    assert not Participant.objects.exists()


# --- API -------------------------------------------------------------------------------------------


def test_api_registration_returns_409_with_the_code(web_client, edition):
    Edition.objects.filter(pk=edition.pk).update(registration_opens_at=timezone.now() + timedelta(days=1))

    response = web_client.post(API_REGISTER_URL, registration_payload(), content_type="application/json")

    assert response.status_code == 409
    assert response.json()["code"] == "REGISTRATION_CLOSED"
    assert "Rejestracja rusza" in response.json()["detail"]
    assert not User.objects.filter(email="nowy@example.test").exists()


def test_current_edition_endpoint_exposes_the_registration_state(web_client, edition):
    closes_at = timezone.now() + timedelta(days=30)
    Edition.objects.filter(pk=edition.pk).update(registration_closes_at=closes_at)

    body = web_client.get("/api/competitions/editions/current/").json()

    assert body["registration"]["is_open"] is True
    assert body["registration"]["reason"] == "open"
    assert body["registration"]["opens_at"] is None
    assert body["registration"]["closes_at"] is not None


# --- logowanie społecznościowe ---------------------------------------------------------------------

GOOGLE_EMAIL = "anna.nowak@example.test"
GOOGLE_PROFILE = {
    "id": "108154321987654321000",
    "email": GOOGLE_EMAIL,
    "verified_email": True,
    "given_name": "Anna",
    "family_name": "Nowak",
}


@pytest.fixture
def google(settings):
    providers = copy.deepcopy(settings.SOCIALACCOUNT_PROVIDERS)
    providers["google"]["APPS"] = [{"client_id": "google-client-id", "secret": "google-secret", "key": ""}]
    settings.SOCIALACCOUNT_PROVIDERS = providers


@contextmanager
def _google_answers():
    def complete_login(self, request, app, token, **kwargs):
        return self.get_provider().sociallogin_from_response(request, GOOGLE_PROFILE)

    with (
        mock.patch.object(
            GoogleOAuth2Adapter, "get_access_token_data", return_value={"access_token": "token-testowy"}
        ),
        mock.patch.object(GoogleOAuth2Adapter, "complete_login", autospec=True, side_effect=complete_login),
    ):
        yield


def _google_login(web_client):
    started = web_client.post("/accounts/google/login/", {})
    state = parse_qs(urlparse(started.headers["Location"]).query)["state"][0]
    with _google_answers():
        return web_client.get("/accounts/google/login/callback/", {"code": "kod", "state": state})


def test_social_signup_is_redirected_to_the_notice_and_creates_nothing(web_client, google, edition):
    Edition.objects.filter(pk=edition.pk).update(registration_enabled=False)
    _google_login(web_client)

    response = web_client.get("/rejestracja/dokoncz/")

    assert response.status_code == 302
    assert response.headers["Location"] == REGISTER_URL
    assert not User.objects.filter(email=GOOGLE_EMAIL).exists()
    assert not SocialAccount.objects.exists()
    assert not Participant.objects.exists()


def test_social_signup_post_is_refused_too(web_client, google, edition):
    Edition.objects.filter(pk=edition.pk).update(registration_enabled=False)
    _google_login(web_client)

    response = web_client.post(
        "/rejestracja/dokoncz/",
        {
            "first_name": "Anna",
            "last_name": "Nowak",
            "school_custom": "on",
            "school": "LO nr 3",
            "district": "mazowieckie",
            "grade": 2,
            "birth_year": 2008,
            "gdpr_consent": "on",
        },
    )

    assert response.status_code == 302
    assert not User.objects.filter(email=GOOGLE_EMAIL).exists()
    assert not SocialAccount.objects.exists()


# --- strona główna i menu --------------------------------------------------------------------------


def test_home_and_nav_show_the_button_when_registration_is_open(web_client, edition):
    content = web_client.get("/").content.decode()

    assert "Zarejestruj się" in content
    assert ">Rejestracja<" in content


def test_home_and_nav_announce_the_start_instead_of_the_button(web_client, edition):
    Edition.objects.filter(pk=edition.pk).update(
        registration_opens_at=timezone.make_aware(timezone.datetime(2027, 9, 8, 0, 0))
    )

    content = web_client.get("/").content.decode()

    assert "Zarejestruj się" not in content
    assert "Rejestracja rusza 8 września 2027" in content
    # Zapowiedź nadal prowadzi na /register/ – tam stoi wyjaśnienie z godziną.
    assert f'href="{REGISTER_URL}"' in content
    assert "Zaloguj się" in content


def test_home_and_nav_hide_the_cta_when_registration_is_disabled(web_client, edition):
    Edition.objects.filter(pk=edition.pk).update(registration_enabled=False)

    content = web_client.get("/").content.decode()

    assert "Zarejestruj się" not in content
    assert "Rejestracja rusza" not in content
    assert f'href="{REGISTER_URL}"' not in content
    # „Zaloguj się” zostaje: konta założone wcześniej działają niezależnie od okna rejestracji.
    assert "Zaloguj się" in content
