"""Rejestracja opiekuna szkolnego jako droga **znajdowalna**, nie tylko istniejąca (22.09.2026).

Zgłoszenie organizatora: rola opiekuna stała w serwisie od wydania z 19.09.2026, ale nie prowadził
do niej żaden odnośnik – kto nie znał adresu ``/register/supervisor/`` z dokumentacji, nie miał jak
go znaleźć. Ten moduł pilnuje trzech rzeczy, których nie widzi
``test_supervisor_registration_flag.py`` (ten patrzy na **ukrycie**, stan domyślny):

- **odnośniki** na ``/register/`` i ``/login/`` pojawiają się dokładnie wtedy, gdy przełącznik jest
  włączony, i nie kosztują zapytania na stronach, które ich nie pokazują,
- **zgody** (regulamin, RODO) są dziś częścią formularza i zostawiają dowód (``ConsentRecord``) tak
  samo, jak u uczestnika – kompletny albo żaden,
- **izolacja konkursów**: profil opiekuna należy do konkursu witryny, pod którą powstał.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.activation import make_activation_token
from apps.accounts.consents import ConsentKind
from apps.accounts.models import ConsentRecord, SchoolSupervisor, User
from apps.web.tests.conftest import captcha_fields, password_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/supervisor/"


def payload(email: str = "nowy.opiekun@szkola.test", **overrides) -> dict:
    data = {
        "email": email,
        "first_name": "Jan",
        "last_name": "Nauczyciel",
        "school": "Zespół Szkół nr 2",
        "terms_consent": "on",
        "gdpr_consent": "on",
        **password_fields(),
        **captcha_fields(),
    }
    data.update(overrides)
    return data


# --- odnośniki ------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/register/", "/login/"])
def test_link_shows_up_when_the_switch_is_on(web_client, supervisor_registration_on, url):
    body = web_client.get(url).content.decode()
    assert REGISTER_URL in body
    assert "opiekun szkolny" in body


def test_the_supervisor_page_links_back_to_participant_registration(web_client, supervisor_registration_on):
    """Kierunek odwrotny stał już wcześniej – test pilnuje, że dołożenie zgód go nie zepsuło."""
    body = web_client.get(REGISTER_URL).content.decode()
    assert reverse("web:register") in body


# --- zgody ------------------------------------------------------------------------------------


def test_registration_records_terms_and_gdpr_consent(web_client, supervisor_registration_on):
    response = web_client.post(REGISTER_URL, payload())

    assert response.status_code == 302
    user = User.objects.get(email="nowy.opiekun@szkola.test")
    supervisor = user.school_supervisor
    records = ConsentRecord.objects.filter(supervisor=supervisor)
    assert {record.kind for record in records} == {ConsentKind.TERMS, ConsentKind.PRIVACY}
    assert all(record.participant_id is None for record in records)
    assert all(record.document_version for record in records)


@pytest.mark.parametrize("missing", ["terms_consent", "gdpr_consent"])
def test_missing_consent_blocks_registration(web_client, supervisor_registration_on, missing):
    response = web_client.post(REGISTER_URL, payload(**{missing: ""}))

    assert response.status_code == 200
    assert User.objects.filter(email="nowy.opiekun@szkola.test").exists() is False


def test_password_mismatch_blocks_registration(web_client, supervisor_registration_on):
    response = web_client.post(REGISTER_URL, payload(password2="inne-haslo-123"))

    assert response.status_code == 200
    assert User.objects.filter(email="nowy.opiekun@szkola.test").exists() is False


# --- po rejestracji: aktywacja i panel ---------------------------------------------------------


def test_activation_unlocks_the_dashboard(web_client, supervisor_registration_on):
    web_client.post(REGISTER_URL, payload())
    user = User.objects.get(email="nowy.opiekun@szkola.test")
    assert user.is_active is False

    token = make_activation_token(user)
    activation_response = web_client.get(reverse("web:activate", args=[token]))
    assert activation_response.status_code == 200

    user.refresh_from_db()
    assert user.is_active is True
    assert user.email_verified_at is not None

    web_client.force_login(user)
    assert web_client.get("/supervisor/").status_code == 200


# --- izolacja konkursów -------------------------------------------------------------------------


def test_the_profile_belongs_to_the_competition_of_the_site(
    client_for, competition, other_competition, supervisor_registration_on
):
    """Opiekun należy do konkursu **witryny, pod którą się zarejestrował** – nie do pierwszej z brzegu."""
    client_for(competition).post(REGISTER_URL, payload(email="pierwszy@szkola.test"))
    client_for(other_competition).post(REGISTER_URL, payload(email="drugi@szkola.test"))

    first = SchoolSupervisor.objects.get(user__email="pierwszy@szkola.test")
    second = SchoolSupervisor.objects.get(user__email="drugi@szkola.test")
    assert first.competition_id == competition.pk
    assert second.competition_id == other_competition.pk

    # Zakresowanie po stronie zapytania (``CompetitionScopedManager``), nie tylko po zapisanej
    # kolumnie: to jest droga, którą listy i panele odczytują profile.
    assert list(SchoolSupervisor.objects.for_competition(competition)) == [first]
    assert list(SchoolSupervisor.objects.for_competition(other_competition)) == [second]
