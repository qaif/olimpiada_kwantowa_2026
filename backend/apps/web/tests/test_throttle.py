"""Limit prób na formularzach HTML (przegląd T-08, ustalenie 1).

Regresja, której pilnują te testy: ``/login/``, ``/register/``, ``/register/committee/`` i upload
z panelu robią to samo, co odpowiadające im endpointy API, więc muszą podlegać temu samemu
limitowi. Wcześniej ``ScopedRateThrottle`` chronił wyłącznie ``/api/…`` – zgadywanie haseł przez
formularz nie było niczym ograniczone.

Stawki podmieniamy przez ``override_settings(REST_FRAMEWORK=…)``, czyli przez tę samą
konfigurację, z której korzysta DRF. To jest część asercji: gdyby formularze miały własne
ustawienie, ten override by na nie nie zadziałał.
"""

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.tests.factories import DEFAULT_PASSWORD
from apps.submissions.tests.factories import pdf_upload
from apps.web.throttle import form_rate, parse_rate

pytestmark = pytest.mark.django_db

LOGIN_URL = "/login/"
REGISTER_URL = "/register/"


def rest_framework_with(**rates) -> dict:
    """Kopia ``REST_FRAMEWORK`` z podmienionymi stawkami – reszta konfiguracji bez zmian."""
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


@pytest.fixture(autouse=True)
def clean_throttle_cache():
    """LocMemCache żyje przez cały przebieg pytest – licznik nie może przeciekać między testami."""
    cache.clear()
    yield
    cache.clear()


def registration_payload(email: str) -> dict:
    return {
        "email": email,
        "password": "Poprawne-Haslo-2026",
        "first_name": "Nowy",
        "last_name": "Uczestnik",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 2008,
        "gdpr_consent": "on",
    }


# --- konfiguracja: jedno źródło stawek dla API i UI --------------------------------------------


def test_parse_rate_understands_the_drf_notation():
    assert parse_rate("10/min") == (10, 60)
    assert parse_rate("10/hour") == (10, 3600)
    assert parse_rate("30/h") == (30, 3600)
    assert parse_rate("5/day") == (5, 86400)
    # ``None`` to umowa z config/settings/test.py: limit wyłączony, a nie „zero żądań”.
    assert parse_rate(None) is None
    assert parse_rate("") is None
    assert parse_rate("bez-ukośnika") is None


def test_form_rate_reads_the_same_setting_as_the_api():
    """Formularz i throttle DRF widzą tę samą wartość – dla każdego wspólnego scope'u."""
    configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    api_throttle = ScopedRateThrottle()

    for scope in ("login", "register", "upload"):
        api_throttle.scope = scope
        assert form_rate(scope) == configured[scope]
        assert form_rate(scope) == api_settings.DEFAULT_THROTTLE_RATES[scope]
        assert form_rate(scope) == api_throttle.get_rate()


@override_settings(REST_FRAMEWORK=rest_framework_with(login="7/min"))
def test_changing_the_api_rate_moves_the_form_limit_too():
    """Nie ma drugiej konfiguracji – podmiana ``REST_FRAMEWORK`` przestawia limit formularza.

    Porównanie idzie do ``settings.REST_FRAMEWORK`` i do ``api_settings``, a nie do
    ``SimpleRateThrottle.THROTTLE_RATES``: ten ostatni jest atrybutem klasy przypisanym w chwili
    importu ``rest_framework.throttling`` i z założenia nie reaguje na ``override_settings``.
    Czytanie ``api_settings`` jest więc ściślejsze, nie luźniejsze.
    """
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"] == "7/min"
    assert api_settings.DEFAULT_THROTTLE_RATES["login"] == "7/min"
    assert form_rate("login") == "7/min"
    assert parse_rate(form_rate("login")) == (7, 60)


# --- logowanie ---------------------------------------------------------------------------------


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_login_form_returns_429_after_the_rate_is_exceeded(web_client, participant):
    email = participant.user.email
    for attempt in range(3):
        response = web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})
        assert response.status_code == 200, attempt

    response = web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "Zbyt wiele prób" in response.content.decode()


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_successful_login_does_not_consume_the_limit(web_client, participant):
    """Liczą się wyłącznie nieudane próby, a udane logowanie zeruje licznik."""
    email = participant.user.email
    for _ in range(2):
        web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})

    response = web_client.post(LOGIN_URL, {"username": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 302

    web_client.logout()
    # Po zerowaniu znowu są trzy próby, a nie jedna – gdyby udane logowanie liczyło się do limitu,
    # pierwsza z nich dałaby 429.
    for attempt in range(3):
        assert web_client.post(LOGIN_URL, {"username": email, "password": "zle"}).status_code == 200, attempt
    assert web_client.post(LOGIN_URL, {"username": email, "password": "zle"}).status_code == 429


def test_rate_none_disables_the_form_limit(web_client, participant):
    """Domyślna konfiguracja testów (``None``) nie włącza licznika ani nie dotyka zegara."""
    email = participant.user.email

    for _ in range(12):
        assert web_client.post(LOGIN_URL, {"username": email, "password": "zle"}).status_code == 200


# --- rejestracja -------------------------------------------------------------------------------


@override_settings(REST_FRAMEWORK=rest_framework_with(register="3/min"))
def test_registration_is_throttled_per_client_address(web_client, edition):
    for attempt in range(3):
        response = web_client.post(REGISTER_URL, registration_payload(f"nowy{attempt}@example.test"))
        assert response.status_code == 302, attempt

    blocked = web_client.post(REGISTER_URL, registration_payload("czwarty@example.test"))
    assert blocked.status_code == 429

    # Zmiana adresu e-mail nie omija limitu, bo jeden z kubełków jest liczony po samym adresie IP.
    # Inny adres klienta ma własny kubełek i nie jest ukarany za cudze próby.
    other_ip = web_client.post(
        REGISTER_URL, registration_payload("zinnegoip@example.test"), REMOTE_ADDR="10.9.9.9"
    )
    assert other_ip.status_code == 302


@override_settings(REST_FRAMEWORK=rest_framework_with(register="3/min"))
def test_committee_registration_shares_the_register_scope(web_client):
    """Rejestracja na kod idzie z tego samego licznika – inaczej limit obchodziłoby się adresem."""
    payload = {
        "email": "komitet@example.test",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Komitet",
        "last_name": "Testowy",
        "invitation_code": "kod-ktorego-nie-ma",
    }
    for attempt in range(3):
        assert web_client.post("/register/committee/", payload).status_code == 200, attempt

    assert web_client.post("/register/committee/", payload).status_code == 429


# --- upload przez UI ---------------------------------------------------------------------------


@override_settings(REST_FRAMEWORK=rest_framework_with(upload="3/min"))
def test_upload_from_the_panel_is_throttled(web_client, participant, entry, problems):
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"
    for attempt in range(3):
        response = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200, attempt

    blocked = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true")

    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    # Odpowiedź na żądanie HTMX to fragment, a nie cała strona z nawigacją.
    content = blocked.content.decode()
    assert "Zbyt wiele prób" in content
    assert "<html" not in content


@override_settings(REST_FRAMEWORK=rest_framework_with(upload="3/min"))
def test_upload_429_is_retargeted_so_htmx_puts_it_in_the_dom(web_client, participant, entry, problems):
    """Dług T-08: 429 z uploadu HTMX ma trafić do DOM, a nie zniknąć w konsoli.

    Serwerowa połowa naprawy to dwa nagłówki: ``HX-Reswap: beforeend`` (komunikat dokleja się
    w karcie zadania, zamiast zastąpić ją razem z formularzem) i ``HX-Retarget`` wyprowadzony
    z ``HX-Target`` samego żądania. Klientowa połowa – włączenie podmiany dla 429 – siedzi
    w ``static/js/app.js`` (``htmx:beforeSwap``).
    """
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"
    target = f"problem-{problems[0].pk}"
    for _ in range(3):
        web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)

    blocked = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)

    assert blocked.status_code == 429
    assert blocked.headers["HX-Reswap"] == "beforeend"
    assert blocked.headers["HX-Retarget"] == f"#{target}"


@override_settings(REST_FRAMEWORK=rest_framework_with(upload="3/min"))
def test_upload_429_ignores_a_target_id_that_is_not_a_plain_identifier(
    web_client, participant, entry, problems
):
    """``HX-Target`` przychodzi od klienta i ląduje w selektorze CSS – kształt jest filtrowany."""
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"
    hostile = "x, body"
    for _ in range(3):
        web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=hostile)

    blocked = web_client.post(url, {"file": pdf_upload()}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=hostile)

    assert blocked.status_code == 429
    assert "HX-Retarget" not in blocked.headers
    # Sama zmiana trybu podmiany zostaje: bez celu htmx użyje domyślnego z atrybutu hx-target.
    assert blocked.headers["HX-Reswap"] == "beforeend"


@override_settings(REST_FRAMEWORK=rest_framework_with(register="3/min"))
def test_non_htmx_429_has_no_htmx_headers(web_client):
    """Zwykły formularz dostaje pełną stronę – nagłówki HTMX byłyby tam bez sensu."""
    for _ in range(3):
        web_client.post(REGISTER_URL, registration_payload("kolejny@example.test"))

    blocked = web_client.post(REGISTER_URL, registration_payload("ostatni@example.test"))

    assert blocked.status_code == 429
    assert "HX-Retarget" not in blocked.headers
    assert "HX-Reswap" not in blocked.headers


@override_settings(REST_FRAMEWORK=rest_framework_with(upload="3/min"))
def test_upload_throttle_does_not_fire_before_the_role_check(web_client, entry, problems):
    """Anonim dostaje 302 na logowanie, a nie 429 – i nie zapełnia licznika uczestnikom."""
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"

    for _ in range(5):
        response = web_client.post(url, {"file": pdf_upload()})
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login/")
