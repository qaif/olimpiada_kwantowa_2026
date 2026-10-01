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
from apps.web import throttle
from apps.web.throttle import form_rate, parse_rate

from .conftest import captcha_fields, password_fields

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
        **password_fields(),
        "first_name": "Nowy",
        "last_name": "Uczestnik",
        "school_custom": "on",
        "school": "LO nr 7",
        "district": "mazowieckie",
        "grade": 2,
        "birth_date": "2008-12-31",
        "phone": "600 100 200",
        "terms_consent": "on",
        "gdpr_consent": "on",
        "guardian_consent": "on",
        # Limit prób i CAPTCHA są niezależnymi warstwami: żeby sprawdzić, że licznik łapie także
        # **udane** rejestracje, formularz musi przechodzić – stąd komplet pól antyspamowych.
        **captcha_fields(),
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


def test_test_settings_list_the_same_throttle_scopes_as_base():
    """Oba słowniki stawek wymieniają **ten sam zestaw** scope'ów – nazwa w nazwę.

    ``ScopedRateThrottle.get_rate`` szuka stawki po nazwie scope'u i brak klucza podnosi u niego
    wyjątek, a nie „limit wyłączony” (``config/settings/test.py`` mówi o tym wprost). Scope dopisany
    w ``base.py`` i pominięty w ``test.py`` wywracałby więc pięćsetką każdy test, który dotknie
    jego widoku – i to komunikatem o nieistniejącej stawce, a nie o ekranie.

    Porównujemy **zestawy nazw**, a nie wartości: wartości mają się różnić (w testach każda jest
    ``None``) i to jest cały sens osobnego pliku ustawień.
    """
    from config.settings import base as base_settings

    assert set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]) == set(
        base_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    )
    # Dwa scope'y wydania K i I wymienione z nazwy, bo o nie poszło: webhook płatności ma widok
    # z jawnym ``throttle_classes``, a kreator ``/setup/`` czyta stawkę z tego samego słownika.
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["payments"] is None


def test_setup_wizard_rate_does_not_depend_on_an_undeclared_scope():
    """Kreator ``/setup/`` ma stawkę także wtedy, gdy jego scope'u nie ma w ustawieniach DRF.

    ``apps.tenancy.setup.throttle_rate`` czyta ustawienia, **gdy znają** scope (także z wartością
    ``None``), a poza tym schodzi na zmienną środowiskową i wartość z § 1.7.1. Dopisanie scope'u
    ``setup`` do obu słowników jest więc zmianą zachowania, a nie porządkiem – i dlatego montaż go
    nie dopisuje, tylko sprawdza, że odwrót działa.
    """
    from apps.tenancy import setup

    assert setup.THROTTLE_SCOPE not in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert setup.throttle_rate() is not None


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


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_parallel_login_attempts_cannot_exceed_the_rate(web_client, participant, monkeypatch):
    """Pakiet 5, A2: próby „w locie” liczą się, zanim hasło zostanie sprawdzone.

    Do tej zmiany ``check()`` stał przed ``consume()``, a zużycie przychodziło dopiero po
    porażce – każda z równoległych prób widziała pusty kubełek i przechodziła. Test odtwarza
    równoległość deterministycznie: **w trakcie** sprawdzania hasła pierwszego żądania (zanim
    cokolwiek zostanie rozstrzygnięte) z tego samego adresu idą trzy kolejne. Stara implementacja
    przepuszczała wszystkie trzy; teraz pierwsze żądanie trzyma rezerwację, więc mieszczą się dwa.
    """
    from django.contrib.auth import forms as auth_forms
    from django.test import Client

    original = auth_forms.authenticate
    inner_statuses: list[int] = []
    state = {"nested": False}
    email = participant.user.email

    def authenticate_while_others_are_in_flight(request=None, **credentials):
        if not state["nested"]:
            state["nested"] = True
            other = Client()
            for _ in range(3):
                inner = other.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})
                inner_statuses.append(inner.status_code)
        return original(request, **credentials)

    monkeypatch.setattr(auth_forms, "authenticate", authenticate_while_others_are_in_flight)

    outer = web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})

    assert outer.status_code == 200
    assert inner_statuses == [200, 200, 429]
    # Wszystkie trzy miejsca są zajęte nieudanymi próbami – kolejna dostaje 429.
    assert web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"}).status_code == 429


@override_settings(REST_FRAMEWORK=rest_framework_with(register="3/min"))
def test_acquire_never_hands_out_more_slots_than_the_rate_even_with_a_stale_view():
    """Zajęcie miejsca rozstrzyga ``cache.add``, a nie wcześniejszy odczyt.

    Odczyt jest tu celowo „przeterminowany” (każde żądanie widzi pusty kubełek – tak, jak widziały
    go równoległe żądania przy starym ``cache.get`` → ``cache.set``). Mimo to przechodzą dokładnie
    trzy, bo każde miejsce da się zająć tylko raz.
    """
    keys = ["web-throttle:register:ip:test-bucket"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(throttle.cache, "get_many", lambda *args, **kwargs: {})
        results = [throttle.acquire("register", keys) for _ in range(5)]

    granted = [slots for wait, slots in results if wait is None]
    assert len(granted) == 3
    assert all(wait is not None and wait > 0 for wait, _ in results[3:])


@override_settings(REST_FRAMEWORK=rest_framework_with(competition_create="2/day"))
def test_unsettled_reservation_goes_back_to_the_pool(rf):
    """Widok z ``throttle_on_request = False``, który nie rozstrzygnął próby, niczego nie zużywa."""
    from django.http import HttpResponse
    from django.views.generic import View

    class PreviewOnly(throttle.ThrottledFormMixin, View):
        throttle_scope = "competition_create"
        throttle_on_request = False

        def post(self, request):
            return HttpResponse("podgląd")

    view = PreviewOnly.as_view()
    for attempt in range(5):
        assert view(rf.post("/x/", REMOTE_ADDR="198.51.100.5")).status_code == 200, attempt

    keys = throttle.throttle_keys("competition_create", rf.post("/x/", REMOTE_ADDR="198.51.100.5"))
    assert throttle.check("competition_create", keys) is None


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_retry_after_still_counts_from_the_oldest_attempt(web_client, participant):
    """Okno pozostaje przesuwne i dokładne: ``Retry-After`` ≈ pełne okno od pierwszej próby."""
    email = participant.user.email
    for _ in range(3):
        web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})

    blocked = web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})

    assert blocked.status_code == 429
    assert 55 <= int(blocked.headers["Retry-After"]) <= 61


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_cache_holds_neither_the_address_nor_the_email(web_client, participant):
    """Klucze to skróty, a wartością miejsca jest sam znacznik czasu (checklista 8.3)."""
    email = participant.user.email
    web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"}, REMOTE_ADDR="198.51.100.77")

    stored = {key: value for key, value in throttle.cache._cache.items() if "web-throttle" in key}
    assert stored
    for key in stored:
        assert email not in key
        assert "198.51.100.77" not in key
    for key in stored:
        assert isinstance(throttle.cache.get(key.split(":", 2)[2]), float)


@override_settings(REST_FRAMEWORK=rest_framework_with(login="3/min"))
def test_cache_outage_lets_requests_through_but_is_logged_once(web_client, participant, monkeypatch, caplog):
    """Awaria Redisa (``IGNORE_EXCEPTIONS`` → ``add`` zwraca ``None``) nie zamyka logowania,
    ale zostawia ślad w logu – raz na minutę na proces, a nie przy każdym żądaniu."""
    monkeypatch.setattr(throttle.cache, "add", lambda *args, **kwargs: None)
    monkeypatch.setattr(throttle, "_last_outage_report", None)
    email = participant.user.email

    with caplog.at_level("ERROR", logger="apps.web.throttle"):
        for attempt in range(6):
            response = web_client.post(LOGIN_URL, {"username": email, "password": "zle-haslo"})
            assert response.status_code == 200, attempt

    outage_records = [record for record in caplog.records if record.name == "apps.web.throttle"]
    assert len(outage_records) == 1
    assert "cache nie odpowiada" in outage_records[0].getMessage()
    assert email not in outage_records[0].getMessage()


def test_chat_and_forum_are_counted_per_account_without_the_address(rf, participant):
    """Pakiet 5, A15: ``chat`` i ``forum`` – jeden kubełek na konto, ten sam z każdego adresu."""
    first = rf.post("/x/", REMOTE_ADDR="198.51.100.1")
    second = rf.post("/x/", REMOTE_ADDR="203.0.113.9")
    first.user = second.user = participant.user

    for scope in ("chat", "forum"):
        assert throttle.user_throttle_keys(scope, first) == throttle.user_throttle_keys(scope, second)
        assert ":user:" in throttle.user_throttle_keys(scope, first)[0]
        assert scope in throttle.PER_USER_SCOPES
    # Pozostałe scope'y zostają przy kubełkach adresu.
    assert "login" not in throttle.PER_USER_SCOPES


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
        **password_fields(),
        "first_name": "Komitet",
        "last_name": "Testowy",
        "invitation_code": "kod-ktorego-nie-ma",
        **captcha_fields(),
    }
    for attempt in range(3):
        assert web_client.post("/register/committee/", payload).status_code == 200, attempt

    assert web_client.post("/register/committee/", payload).status_code == 429


@override_settings(REST_FRAMEWORK=rest_framework_with(register="3/min"))
def test_supervisor_registration_shares_the_register_scope(web_client, supervisor_registration_on):
    """Rejestracja opiekuna idzie z tego samego licznika, co uczestnik i komitet.

    Payload celowo bez zgód (``terms_consent``/``gdpr_consent``): serwis odrzuca go za każdym
    razem tym samym błędem (``CONSENT_REQUIRED``), więc odpowiedź jest przewidywalna – 200
    (formularz z błędem) na każdą z trzech prób, 429 na czwartą. Sam POST konsumuje limit
    niezależnie od wyniku (``RegisterSupervisorView.throttle_scope = "register"``).
    """
    payload = {
        "email": "opiekun.throttle@example.test",
        "first_name": "Jan",
        "last_name": "Nauczyciel",
        "school": "Zespół Szkół nr 2",
        **password_fields(),
        **captcha_fields(),
    }
    for attempt in range(3):
        assert web_client.post("/register/supervisor/", payload).status_code == 200, attempt

    assert web_client.post("/register/supervisor/", payload).status_code == 429


# --- upload przez UI ---------------------------------------------------------------------------


@override_settings(REST_FRAMEWORK=rest_framework_with(upload="3/min"))
def test_upload_from_the_panel_is_throttled(web_client, participant, entry, problems):
    web_client.force_login(participant.user)
    url = f"/me/stages/{entry.stage_id}/problems/1/upload/"
    for attempt in range(3):
        response = web_client.post(url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200, attempt

    blocked = web_client.post(url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true")

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
        web_client.post(
            url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target
        )

    blocked = web_client.post(
        url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target
    )

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
        web_client.post(
            url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=hostile
        )

    blocked = web_client.post(
        url, {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=hostile
    )

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
        response = web_client.post(url, {"file": pdf_upload(), "confirmed": "1"})
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login/")
