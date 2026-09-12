"""Logowanie i rejestracja przez Google/Facebooka – bez ani jednego żądania do sieci.

Jak to jest testowane: przechodzimy **prawdziwą** ścieżką HTTP (POST na adres logowania dostawcy,
potem GET na adres powrotny), a podmieniamy wyłącznie te dwa miejsca, w których allauth naprawdę
wychodzi na zewnątrz – wymianę kodu na token (``get_access_token_data``) i odczyt profilu
(``complete_login``). Dzięki temu w teście uczestniczy wszystko, co ma znaczenie: parametr
``state`` z sesji, nasze adaptery, przepływ rejestracji z sesyjnym „loginem oczekującym”,
przekierowania i szablony.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.facebook.views import FacebookOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from django.test import Client

from apps.accounts.models import GROUP_PARTICIPANT, Participant, User
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.core.models import AuditLog
from apps.schools.tests.factories import SchoolFactory

pytestmark = pytest.mark.django_db

GOOGLE_EMAIL = "anna.nowak@example.test"
FACEBOOK_EMAIL = "piotr.lis@example.test"

#: Odpowiedź ``/oauth2/v2/userinfo`` Google. ``verified_email`` decyduje, czy adres jest u nas
#: traktowany jako potwierdzony – i tym samym, czy wolno połączyć go z istniejącym kontem.
GOOGLE_PROFILE = {
    "id": "108154321987654321000",
    "email": GOOGLE_EMAIL,
    "verified_email": True,
    "given_name": "Anna",
    "family_name": "Nowak",
    "name": "Anna Nowak",
}

#: Odpowiedź Graph API. Facebook nie mówi, czy adres jest potwierdzony – i tak go nie honorujemy.
FACEBOOK_PROFILE = {
    "id": "10223344556677889",
    "email": FACEBOOK_EMAIL,
    "first_name": "Piotr",
    "last_name": "Lis",
    "name": "Piotr Lis",
}

PROVIDER_ADAPTERS = {"google": GoogleOAuth2Adapter, "facebook": FacebookOAuth2Adapter}
PROVIDER_PROFILES = {"google": GOOGLE_PROFILE, "facebook": FACEBOOK_PROFILE}

SIGNUP_URL = "/rejestracja/dokoncz/"


def _enable(settings, *provider_ids: str) -> None:
    """Dokłada klucze dostawcom – jak zmienne środowiskowe w produkcji, tylko na czas testu."""
    providers = copy.deepcopy(settings.SOCIALACCOUNT_PROVIDERS)
    for provider_id in provider_ids:
        providers[provider_id]["APPS"] = [
            {"client_id": f"{provider_id}-client-id", "secret": f"{provider_id}-secret", "key": ""}
        ]
    settings.SOCIALACCOUNT_PROVIDERS = providers


@pytest.fixture
def google(settings):
    _enable(settings, "google")


@pytest.fixture
def facebook(settings):
    _enable(settings, "facebook")


@contextmanager
def _provider_answers(provider_id: str, profile: dict):
    """Podmienia dwa wyjścia do sieci: wymianę kodu na token i odczyt profilu."""
    adapter_class = PROVIDER_ADAPTERS[provider_id]

    def complete_login(self, request, app, token, **kwargs):
        return self.get_provider().sociallogin_from_response(request, profile)

    with (
        mock.patch.object(
            adapter_class, "get_access_token_data", return_value={"access_token": "token-testowy"}
        ),
        mock.patch.object(adapter_class, "complete_login", autospec=True, side_effect=complete_login),
    ):
        yield


def social_login(client: Client, provider_id: str = "google", profile: dict | None = None, **extra):
    """Pełny przelot: POST na logowanie dostawcy → GET na adres powrotny z ``code`` i ``state``."""
    started = client.post(f"/accounts/{provider_id}/login/", extra)
    assert started.status_code == 302, "POST na logowanie dostawcy ma przekierować na ekran zgody"
    state = parse_qs(urlparse(started.headers["Location"]).query)["state"][0]
    with _provider_answers(provider_id, profile or PROVIDER_PROFILES[provider_id]):
        return client.get(f"/accounts/{provider_id}/login/callback/", {"code": "kod-testowy", "state": state})


def complete_signup(client: Client, **overrides):
    """Wypełnia i wysyła nasz formularz dokończenia rejestracji."""
    data = {
        "first_name": "Anna",
        "last_name": "Nowak",
        "school_custom": "on",
        "school": "LO nr 3",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 2008,
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        # Rocznik 2008 to w 2026 r. osoba „na pewno niepełnoletnia” w rozumieniu
        # ``accounts.consents.is_minor`` – bez zgody opiekuna formularz jej nie przepuści.
        "guardian_consent": "on",
    }
    data.update(overrides)
    return client.post(SIGNUP_URL, data)


# --- (a) widoczność przycisków ----------------------------------------------------------------


def test_login_page_has_no_provider_buttons_without_keys(web_client):
    """Instalacja bez kluczy wygląda dokładnie tak, jak przed dodaniem logowania społecznościowego."""
    body = web_client.get("/login/").content.decode()

    assert "Lub kontynuuj z" not in body
    assert "/accounts/google/login/" not in body
    assert "/accounts/facebook/login/" not in body


def test_login_and_register_pages_show_configured_providers(web_client, google, facebook, edition):
    for url in ("/login/", "/register/"):
        body = web_client.get(url).content.decode()

        assert "Lub kontynuuj z" in body
        assert 'action="/accounts/google/login/"' in body
        assert 'action="/accounts/facebook/login/"' in body
        # Przycisk jest POST-em z tokenem CSRF – GET dałby się wywołać z obcej strony.
        assert "csrfmiddlewaretoken" in body


def test_only_the_configured_provider_gets_a_button(web_client, google):
    body = web_client.get("/login/").content.decode()

    assert 'action="/accounts/google/login/"' in body
    assert "/accounts/facebook/login/" not in body


# --- (b) lokalne widoki allauth nie istnieją ---------------------------------------------------


@pytest.mark.parametrize("url", ["/accounts/login/", "/accounts/signup/", "/accounts/"])
def test_allauth_local_account_views_are_not_mounted(web_client, url, google):
    """Druga ścieżka logowania/rejestracji hasłem byłaby ścieżką bez naszego limitu prób."""
    assert web_client.get(url).status_code == 404


@pytest.mark.parametrize("url", ["/accounts/google/login/", "/accounts/facebook/login/callback/"])
def test_provider_urls_without_keys_are_404_not_500(web_client, url):
    """Adres dostawcy bez kluczy to adres, którego nie ma – nie awaria aplikacji."""
    assert web_client.get(url).status_code == 404
    assert web_client.post(url).status_code == 404


def test_login_by_token_endpoint_is_not_mounted(web_client, google):
    """Logowanie tokenem z SDK (One Tap) nie jest używane, więc nie jest wystawione."""
    assert web_client.post("/accounts/google/login/token/").status_code == 404


def test_our_login_form_is_the_only_password_login(web_client):
    """``reverse("account_login")`` allauth wskazuje nasz formularz, a nie widok allauth."""
    from django.urls import reverse

    assert reverse("account_login") == "/login/"
    assert web_client.get("/login/").resolver_match.view_name == "web:login"


# --- (c) nowy użytkownik: dokończenie rejestracji ----------------------------------------------


def test_new_google_user_is_sent_to_our_signup_form_without_creating_an_account(web_client, google):
    response = social_login(web_client)

    assert response.status_code == 302
    assert response.headers["Location"] == SIGNUP_URL
    # Kluczowa asercja: po samym OAuth w bazie nie ma jeszcze niczego.
    assert not User.objects.filter(email=GOOGLE_EMAIL).exists()
    assert not SocialAccount.objects.exists()


def test_signup_form_shows_the_provider_email_and_prefills_the_name(web_client, google, edition):
    social_login(web_client)

    body = web_client.get(SIGNUP_URL).content.decode()

    assert GOOGLE_EMAIL in body
    assert 'value="Anna"' in body
    assert 'value="Nowak"' in body
    # Adresu nie da się podmienić – gdyby był polem formularza, dałoby się założyć konto na cudzy.
    assert 'name="email"' not in body


def test_signup_without_gdpr_consent_creates_nothing(web_client, google, edition):
    social_login(web_client)

    response = complete_signup(web_client, gdpr_consent="")

    assert "Zgoda na przetwarzanie danych osobowych jest wymagana." in response.content.decode()
    assert not User.objects.filter(email=GOOGLE_EMAIL).exists()
    assert not Participant.objects.exists()
    assert not SocialAccount.objects.exists()


def test_signup_without_gdpr_consent_can_be_retried(web_client, google, edition):
    """Błąd zgody nie może wyrzucić z przepływu – login społecznościowy zostaje w sesji."""
    social_login(web_client)
    complete_signup(web_client, gdpr_consent="")

    response = complete_signup(web_client)

    assert response.status_code == 302
    assert User.objects.filter(email=GOOGLE_EMAIL).exists()


def test_signup_with_consent_creates_participant_linked_to_the_provider(web_client, google, edition):
    social_login(web_client)

    response = complete_signup(web_client, school="LO nr 3", district="mazowieckie", birth_year=2009)

    assert response.status_code == 302
    assert response.headers["Location"] == "/me/"

    user = User.objects.get(email=GOOGLE_EMAIL)
    assert user.first_name == "Anna"
    assert user.last_name == "Nowak"
    assert list(user.groups.values_list("name", flat=True)) == [GROUP_PARTICIPANT]
    # Konto założone przez dostawcę nie ma hasła: poświadczeniem jest konto u Google.
    assert not user.has_usable_password()

    participant = Participant.objects.get(user=user)
    assert participant.school == "LO nr 3"
    assert participant.district == "mazowieckie"
    assert participant.birth_year == 2009
    assert participant.gdpr_consent_at is not None
    assert participant.public_code.startswith("OLM-")

    account = SocialAccount.objects.get(user=user)
    assert account.provider == "google"
    assert account.uid == GOOGLE_PROFILE["id"]


def test_signup_can_pick_a_school_from_the_directory(web_client, google, edition):
    """Ta sama droga co w rejestracji hasłem: wybór ze słownika wiąże profil z rejestrem."""
    school = SchoolFactory(name="XIV LICEUM OGÓLNOKSZTAŁCĄCE", city="Warszawa")
    social_login(web_client)

    response = complete_signup(web_client, school_custom="", school="", school_id=str(school.id), grade=4)

    assert response.status_code == 302
    participant = Participant.objects.get(user__email=GOOGLE_EMAIL)
    assert participant.school_ref == school
    assert participant.school == "XIV LICEUM OGÓLNOKSZTAŁCĄCE"
    assert participant.grade == 4


def test_signup_is_audited_without_personal_data(web_client, google, edition):
    social_login(web_client)
    complete_signup(web_client)

    entry = AuditLog.objects.get(action="account.social_signup")
    # ``email_verified`` mówi, czy dostawca potwierdził adres – czyli czy konto powstało aktywne,
    # czy przeszło przez nasz link aktywacyjny. To fakt o **drodze** logowania, nie dana osobowa.
    assert entry.diff == {"provider": "google", "email_verified": True}
    assert GOOGLE_EMAIL not in str(entry.diff)


def test_signup_page_is_unreachable_without_a_pending_social_login(web_client):
    """Bez przejścia przez dostawcę formularz nie jest drogą do założenia konta."""
    response = web_client.get(SIGNUP_URL)

    assert response.status_code == 302
    assert response.headers["Location"] == "/login/"
    assert web_client.post(SIGNUP_URL, {"gdpr_consent": "on"}).status_code == 302
    assert not User.objects.exists()


def test_second_google_login_reuses_the_linked_account(web_client, google, edition):
    social_login(web_client)
    complete_signup(web_client)
    web_client.post("/logout/")

    response = social_login(web_client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/me/"
    assert User.objects.filter(email=GOOGLE_EMAIL).count() == 1
    assert SocialAccount.objects.count() == 1


# --- (d) istniejące konto + Google ze zweryfikowanym adresem -----------------------------------


def test_verified_google_email_connects_to_the_existing_account(web_client, google):
    participant = ParticipantFactory(user=UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT]))

    response = social_login(web_client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/me/"
    assert User.objects.filter(email=GOOGLE_EMAIL).count() == 1
    assert Participant.objects.count() == 1
    assert SocialAccount.objects.get(user=participant.user).provider == "google"

    entry = AuditLog.objects.get(action="login.social_connect")
    assert entry.actor == participant.user
    assert entry.diff["provider"] == "google"


def test_auto_connect_wipes_the_unverified_accounts_password(web_client, google):
    """Zachowanie allauth, świadomie zostawione – uzasadnienie w apps/accounts/adapters.py.

    Adresu przy rejestracji hasłem nie weryfikujemy, więc konto mogło zostać założone na cudzy
    adres. Po zalogowaniu przez Google właściciel adresu ustawia hasło na nowo resetem.
    """
    participant = ParticipantFactory(user=UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT]))
    assert participant.user.has_usable_password()

    social_login(web_client)

    participant.user.refresh_from_db()
    assert not participant.user.has_usable_password()
    assert AuditLog.objects.get(action="login.social_connect").diff["password_wiped"] is True


def test_unverified_google_email_does_not_take_over_an_existing_account(web_client, google):
    """Bez ``verified_email`` adres jest tylko deklaracją – konto zostaje nietknięte."""
    user = UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT])
    profile = {**GOOGLE_PROFILE, "verified_email": False}

    response = social_login(web_client, profile=profile)

    assert response.status_code == 401
    assert "Konto z tym adresem już istnieje" in response.content.decode()
    assert not SocialAccount.objects.exists()
    user.refresh_from_db()
    assert user.has_usable_password()


# --- (e) Facebook nie przejmuje kont -----------------------------------------------------------


def test_facebook_never_connects_to_an_existing_account_by_email(web_client, facebook):
    user = UserFactory(email=FACEBOOK_EMAIL, groups=[GROUP_PARTICIPANT])

    response = social_login(web_client, "facebook")

    assert response.status_code == 401
    body = response.content.decode()
    assert "Konto z tym adresem już istnieje" in body
    assert "Ustaw nowe hasło" in body
    assert not SocialAccount.objects.exists()
    user.refresh_from_db()
    # Ani powiązania, ani wyczyszczonego hasła: konto nie zmieniło stanu.
    assert user.has_usable_password()
    assert not AuditLog.objects.filter(action="login.social_connect").exists()


def test_facebook_can_still_create_a_brand_new_account(web_client, facebook, edition):
    response = social_login(web_client, "facebook")

    assert response.headers["Location"] == SIGNUP_URL
    complete_signup(web_client, first_name="Piotr", last_name="Lis")

    user = User.objects.get(email=FACEBOOK_EMAIL)
    assert SocialAccount.objects.get(user=user).provider == "facebook"


def test_provider_without_an_email_is_refused(web_client, facebook):
    """E-mail jest u nas loginem i jedyną drogą odzyskania konta – bez niego nie ma rejestracji."""
    profile = {key: value for key, value in FACEBOOK_PROFILE.items() if key != "email"}

    response = social_login(web_client, "facebook", profile=profile)

    assert response.status_code == 401
    assert "Brak adresu e-mail" in response.content.decode()
    assert not User.objects.exists()


# --- (f) konto nieaktywne ----------------------------------------------------------------------


def test_inactive_account_cannot_log_in_with_a_linked_provider(web_client, google):
    user = UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT])
    ParticipantFactory(user=user)
    SocialAccount.objects.create(user=user, provider="google", uid=GOOGLE_PROFILE["id"])
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = social_login(web_client)

    assert response.status_code == 401
    assert "Konto jest nieaktywne" in response.content.decode()
    assert "_auth_user_id" not in web_client.session


def test_inactive_account_is_not_connected_by_a_verified_email(web_client, google):
    """Odmowa zapada **przed** powiązaniem konta: inaczej wyłączone konto i tak zmieniałoby stan."""
    user = UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT])
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = social_login(web_client)

    assert response.status_code == 401
    assert not SocialAccount.objects.exists()
    user.refresh_from_db()
    assert user.has_usable_password()


# --- (g) role: przekierowanie po zalogowaniu ---------------------------------------------------


def test_committee_member_lands_in_the_review_panel(web_client, google):
    """Konta komitetu nie powstają przez OAuth, ale istniejący recenzent może się tak zalogować."""
    reviewer = ActiveReviewerFactory(user=UserFactory(email=GOOGLE_EMAIL, groups=["reviewer"]))

    response = social_login(web_client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/review/"
    assert SocialAccount.objects.get(user=reviewer.user).provider == "google"


def test_next_parameter_wins_over_the_role_panel(web_client, google):
    ParticipantFactory(user=UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT]))

    response = social_login(web_client, next="/results/1/")

    assert response.headers["Location"] == "/results/1/"


def test_open_redirect_through_next_is_rejected(web_client, google):
    ParticipantFactory(user=UserFactory(email=GOOGLE_EMAIL, groups=[GROUP_PARTICIPANT]))

    response = social_login(web_client, next="https://zlosliwy.example/przejmij")

    assert response.headers["Location"] == "/me/"


# --- (h) nagłówki bezpieczeństwa ---------------------------------------------------------------


def test_pages_of_the_social_flow_keep_the_nonce_only_script_policy(web_client, google):
    social_login(web_client)

    for url in (SIGNUP_URL, "/accounts/google/login/", "/accounts/login/error/"):
        policy = web_client.get(url)["Content-Security-Policy"]

        assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
        assert "'unsafe-eval'" not in policy


def test_form_action_lists_only_the_enabled_providers(web_client, google):
    policy = web_client.get("/login/")["Content-Security-Policy"]
    form_action = policy.split("form-action ")[1].split(";")[0]

    assert form_action == "'self' https://accounts.google.com"


def test_form_action_stays_self_without_oauth(web_client):
    policy = web_client.get("/login/")["Content-Security-Policy"]

    assert "form-action 'self';" in policy


def test_provider_login_does_nothing_on_get(web_client, google):
    """GET nie rusza uścisku dłoni – inaczej obca strona mogłaby wywołać logowanie (login CSRF)."""
    response = web_client.get("/accounts/google/login/")

    assert response.status_code == 200
    assert "Zaloguj przez Google" in response.content.decode()
    assert "csrfmiddlewaretoken" in response.content.decode()
