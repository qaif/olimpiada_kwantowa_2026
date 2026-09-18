"""Kreator pierwszego uruchomienia ``/setup/`` (``docs/UNIWERSALNY-ETAP-2.md`` § 1.7.1, D20).

Przedmiotem tych testów jest przede wszystkim **bramka**, a nie wygoda kreatora: to jedyny
publiczny adres w serwisie, który zakłada konto z pełnymi uprawnieniami, więc każdy warunek
wpuszczenia ma tu własny test, a każdy warunek odmowy – własną asercję na 404.

Baza testowa ma Konkurs #1 (migracja ``tenancy.0002``), czyli stan **produkcyjny**: kreatora nie
ma. Instalację świeżą odtwarza fikstura :func:`empty_install`, kasując konkursy i superużytkowników
w transakcji testu. To jest odwrotność zwykłego przygotowania danych i dlatego stoi w jednym
miejscu: test, który kasowałby konkurs sam, byłby testem, który przy okazji opisuje, co jeszcze
wolno skasować.
"""

from __future__ import annotations

import time

import pytest

from apps.tenancy import setup

pytestmark = pytest.mark.django_db

#: Token kreatora w testach. Wartość jest nieistotna, ważne, że test podaje **ten** napis,
#: a kreator czyta go ze zmiennej środowiskowej ``SETUP_TOKEN`` (tak samo, jak z ``.env``).
TOKEN = "token-testowy-kreatora"

SETUP_URL = "/setup/"
COMPETITION_URL = "/setup/konkurs/"
DONE_URL = "/setup/gotowe/"

OPERATOR_EMAIL = "operator@example.org"
OPERATOR_PASSWORD = "Poprawne-Haslo-2026"

#: Odpowiedź przyjmowana przez ``django-simple-captcha`` przy ``CAPTCHA_TEST_MODE``
#: (``config/settings/test.py``). Pola wypisujemy tu, a nie importujemy z konftestu warstwy WWW:
#: ``apps/web/tests/conftest.py`` należy do innego zadania, a kreator ma nie zależeć od jego
#: pomocników. Kształt pól jest kontraktem ``apps/web/captcha.py``, nie tamtego pliku.
CAPTCHA_TEST_RESPONSE = "PASSED"


def antispam_fields(*, elapsed: int = 10, honeypot: str = "") -> dict:
    """Blok antyspamowy kroku 1.: CAPTCHA, pułapka i podpisany znacznik czasu."""
    from apps.web.captcha import sign_timestamp

    return {
        "captcha_0": "klucz-nieistotny-w-trybie-testowym",
        "captcha_1": CAPTCHA_TEST_RESPONSE,
        "website": honeypot,
        "form_ts": sign_timestamp(time.time() - elapsed),
    }


def operator_payload(**overrides) -> dict:
    data = {
        "email": OPERATOR_EMAIL,
        "first_name": "Anna",
        "last_name": "Operatorska",
        "password1": OPERATOR_PASSWORD,
        "password2": OPERATOR_PASSWORD,
        **antispam_fields(),
    }
    data.update(overrides)
    return data


def competition_payload(**overrides) -> dict:
    data = {
        "name": "Olimpiada Fizyczna",
        "short_name": "Olimpiada Fizyczna",
        "slug": "fizyczna",
        "domain": "fizyczna.invalid",
        "organizer": "Fundacja Testowa",
        "contact_email": "kontakt@example.org",
        # Szablon „pusty” nie ma ``safe_seeds``, więc test nie wczytuje dwumegabajtowego wykazu
        # szkół. Zawartość szablonów sprawdza ``test_create_competition.py``.
        "template": "pusty",
    }
    data.update(overrides)
    return data


@pytest.fixture
def empty_install(db, monkeypatch):
    """Instalacja świeża: bez konkursu, bez superużytkownika, z tokenem w środowisku.

    Limit prób jest tu **wyłączony** pustą stawką – tak samo, jak ``config/settings/test.py``
    wyłącza limity pozostałych scope'ów. Test, który limit sprawdza, ustawia stawkę sam.
    """
    from apps.accounts.models import User
    from apps.tenancy.models import Competition

    Competition.objects.all().delete()
    User.objects.filter(is_superuser=True).delete()
    monkeypatch.setenv(setup.SETUP_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(setup.SETUP_THROTTLE_RATE_ENV, "")
    return TOKEN


@pytest.fixture
def opened(client, empty_install):
    """Klient, który pokazał już token – czyli przeglądarka stojąca na kroku 1."""
    client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")
    return client


# --- bramka -------------------------------------------------------------------------------------


def test_setup_is_404_when_a_competition_exists(client, competition, monkeypatch):
    """Punkt 22 listy kontrolnej § 0.5: na produkcji kreatora nie ma pod żadnym adresem."""
    monkeypatch.setenv(setup.SETUP_TOKEN_ENV, TOKEN)

    assert client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}").status_code == 404
    assert client.get(SETUP_URL).status_code == 404
    assert client.get(COMPETITION_URL).status_code == 404
    assert client.get(DONE_URL).status_code == 404
    assert competition.pk is not None


def test_setup_is_404_when_a_superuser_exists(client, empty_install, django_user_model):
    """Sam brak konkursu nie otwiera kreatora: instalacja po przywróceniu kopii ma konta."""
    django_user_model.objects.create_user(
        email="stary@example.org", password=OPERATOR_PASSWORD, is_superuser=True, is_staff=True
    )

    response = client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")

    assert response.status_code == 404


def test_setup_is_404_without_the_token(client, empty_install):
    """Pusta baza nie wystarcza: publiczny adres bez tokenu nie zakłada superużytkownika."""
    assert client.get(SETUP_URL).status_code == 404
    assert client.post(SETUP_URL, operator_payload()).status_code == 404


def test_setup_is_404_for_a_wrong_token(client, empty_install):
    assert client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}=nie-ten").status_code == 404


def test_the_token_moves_from_the_address_to_the_session(client, empty_install):
    """Token wchodzi do sesji i znika z adresu – nie zostaje w historii ani w ``Referer``."""
    response = client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")

    assert response.status_code == 302
    assert response["Location"] == SETUP_URL
    assert client.session[setup.SESSION_TOKEN_KEY] is True
    assert client.get(SETUP_URL).status_code == 200


# --- przebieg -----------------------------------------------------------------------------------


def test_the_wizard_creates_the_operator_the_competition_and_the_audit_entry(opened):
    """Pełny przebieg: krok 1., krok 2., podsumowanie – i stan bazy, który z nich wynika."""
    from apps.accounts.models import CompetitionRole, Membership, User
    from apps.core.models import AuditLog
    from apps.tenancy.models import Competition

    first = opened.post(SETUP_URL, operator_payload())
    assert first.status_code == 302
    assert first["Location"] == COMPETITION_URL

    operator = User.objects.get(email=OPERATOR_EMAIL)
    assert operator.is_superuser and operator.is_staff
    assert operator.first_name == "Anna"
    assert operator.groups.filter(name="coordinator").exists()
    # Operator jest zalogowany od kroku 1.: kroki 2.–3. stoją na koncie, a nie na tym, że ktoś
    # trzyma token (patrz docstring apps/tenancy/setup_views.py).
    assert opened.get(COMPETITION_URL).status_code == 200

    second = opened.post(COMPETITION_URL, competition_payload())
    assert second.status_code == 302
    assert second["Location"] == DONE_URL

    created = Competition.objects.get(slug="fizyczna")
    assert created.name == "Olimpiada Fizyczna"
    assert created.primary_domain == "fizyczna.invalid"
    assert Membership.objects.filter(
        user=operator, competition=created, role=CompetitionRole.COORDINATOR
    ).exists()
    assert AuditLog.objects.filter(action=setup.AUDIT_ACTION_COMPLETED).count() == 1

    done = opened.get(DONE_URL)
    assert done.status_code == 200
    assert "Olimpiada Fizyczna" in done.content.decode()

    # Instalacja skonfigurowana = kreatora nie ma. Także dla tej samej przeglądarki.
    assert opened.get(SETUP_URL).status_code == 404


def test_setup_creates_exactly_one_operator_under_concurrency(client, empty_install):
    """Dwie przeglądarki otwarte na kroku 1.: druga dostaje 404, a nie drugiego superużytkownika.

    Bramka jest sprawdzana w ``POST`` i jeszcze raz pod blokadą doradczą, więc przewaga czasowa
    między wyrenderowaniem formularza a jego wysłaniem niczego nie otwiera.
    """
    from django.test import Client

    from apps.accounts.models import User

    other = Client()
    client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")
    other.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")
    assert other.get(SETUP_URL).status_code == 200

    first = client.post(SETUP_URL, operator_payload())
    second = other.post(SETUP_URL, operator_payload(email="drugi@example.org"))

    assert first.status_code == 302
    assert second.status_code == 404
    assert User.objects.filter(is_superuser=True).count() == 1


def test_the_second_step_refuses_a_browser_without_the_operator_session(opened):
    """Krok 2. wpuszcza wyłącznie konto założone w kroku 1. tej sesji, a nie każdego z tokenem."""
    from django.test import Client

    opened.post(SETUP_URL, operator_payload())
    intruder = Client()
    intruder.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")

    assert intruder.get(COMPETITION_URL).status_code == 404


def test_the_captcha_answer_is_required(opened):
    """Blok antyspamowy jest ten sam, co w publicznej rejestracji – i tak samo odmawia."""
    from apps.accounts.models import User

    response = opened.post(SETUP_URL, operator_payload(captcha_1="nie-ta-odpowiedz"))

    assert response.status_code == 400
    assert not User.objects.filter(email=OPERATOR_EMAIL).exists()


def test_a_weak_password_is_a_form_error_not_a_crash(opened):
    from apps.accounts.models import User

    response = opened.post(SETUP_URL, operator_payload(password1="haslo", password2="haslo"))

    assert response.status_code == 400
    assert not User.objects.filter(email=OPERATOR_EMAIL).exists()


def test_a_taken_domain_comes_back_as_a_form_error(opened, monkeypatch):
    """Odmowa komendy jest komunikatem dla człowieka, a nie błędem serwera."""
    from wagtail.models import Site

    from apps.tenancy.models import Competition

    opened.post(SETUP_URL, operator_payload())
    taken = Site.objects.first().hostname

    response = opened.post(COMPETITION_URL, competition_payload(domain=taken))

    assert response.status_code == 400
    assert not Competition.objects.exists()


# --- limit prób ---------------------------------------------------------------------------------


def test_the_rate_limit_answers_429_after_the_rate_is_spent(client, empty_install, monkeypatch):
    """Limit chroni krok zakładający konto – stawka 10/h z § 1.7.1, tu skrócona do dwóch prób."""
    monkeypatch.setenv(setup.SETUP_THROTTLE_RATE_ENV, "2/hour")
    client.get(f"{SETUP_URL}?{setup.TOKEN_QUERY_PARAM}={TOKEN}")
    payload = operator_payload(password1="haslo", password2="haslo")

    assert client.post(SETUP_URL, payload).status_code == 400
    assert client.post(SETUP_URL, payload).status_code == 400
    refused = client.post(SETUP_URL, payload)

    assert refused.status_code == 429
    assert refused["Retry-After"]


# --- /status.json ---------------------------------------------------------------------------------


def test_status_json_reports_a_pending_setup_only_on_an_empty_installation(client, empty_install):
    assert client.get("/status.json").json()["setup_pending"] is True


def test_status_json_reports_no_pending_setup_on_a_configured_installation(client, competition):
    assert client.get("/status.json").json()["setup_pending"] is False
