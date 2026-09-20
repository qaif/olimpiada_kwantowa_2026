"""Ekran „Przekazywanie rozwiązań” ``/coordinator/submission-forwarding/``.

Przedmiotem jest to, czego nie widać po kodzie widoku:

- konkurs zaczyna z **pustym** polem, czyli z wyłączonym przekazywaniem, a ekran mówi o tym wprost,
- adres z literówką i szósty adres odpadają **pod polem**, a w bazie nie zostaje nic – bo błąd
  w tym polu znaczy pracę uczestnika wysłaną w próżnię,
- zapis zostawia ślad ``competition.forwarding_updated`` z **liczbą** adresów i bez ani jednego
  adresu, a zapis bez zmiany nie zostawia śladu w ogóle,
- ekran należy do koordynatora i do nikogo więcej, a zapis dotyczy **konkursu z domeny żądania** –
  nie ma tu adresu, pod którym dałoby się ruszyć konfigurację sąsiada.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory
from apps.core.models import AuditLog
from apps.tenancy.models import MAX_FORWARD_EMAILS
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

URL = "/coordinator/submission-forwarding/"
FORWARD_TO = "komitet@example.invalid"


@pytest.fixture
def coordinator_client(client_for, competition):
    """Zalogowany koordynator Konkursu #1, wysyłający żądania pod jego domenę."""
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def audit_entries():
    return AuditLog.objects.filter(action="competition.forwarding_updated")


# --- odczyt ---------------------------------------------------------------------------------------


def test_the_screen_starts_with_forwarding_off(coordinator_client, competition):
    assert competition.forward_emails == []

    content = coordinator_client.get(URL).content.decode()

    assert "wyłączone" in content
    assert 'name="submission_forward_emails"' in content


def test_the_screen_lists_the_configured_addresses(coordinator_client, competition):
    competition.submission_forward_emails = FORWARD_TO
    competition.save(update_fields=["submission_forward_emails"])

    content = coordinator_client.get(URL).content.decode()

    assert FORWARD_TO in content
    assert "włączone" in content


# --- zapis ----------------------------------------------------------------------------------------


def test_saving_addresses_writes_them_and_leaves_an_audit_entry(coordinator_client, competition):
    response = coordinator_client.post(
        URL, {"submission_forward_emails": f"{FORWARD_TO}, drugi@example.invalid"}
    )

    competition.refresh_from_db()
    assert response.status_code == 302
    assert response.headers["Location"] == URL
    # Normalizacja: wklejona lista po przecinku zapisuje się po jednym adresie na wiersz.
    assert competition.submission_forward_emails == f"{FORWARD_TO}\ndrugi@example.invalid"
    assert competition.forward_emails == [FORWARD_TO, "drugi@example.invalid"]
    entry = audit_entries().get()
    assert entry.diff == {"recipients": 2}
    assert FORWARD_TO not in str(entry.diff)


def test_clearing_the_field_turns_forwarding_off(coordinator_client, competition):
    competition.submission_forward_emails = FORWARD_TO
    competition.save(update_fields=["submission_forward_emails"])

    coordinator_client.post(URL, {"submission_forward_emails": "   "})

    competition.refresh_from_db()
    assert competition.forward_emails == []
    assert audit_entries().get().diff == {"recipients": 0}


def test_saving_the_same_addresses_again_leaves_no_trace(coordinator_client, competition):
    competition.submission_forward_emails = FORWARD_TO
    competition.save(update_fields=["submission_forward_emails"])

    coordinator_client.post(URL, {"submission_forward_emails": f" {FORWARD_TO} "})

    assert not audit_entries().exists()


def test_duplicates_are_collapsed(coordinator_client, competition):
    coordinator_client.post(URL, {"submission_forward_emails": f"{FORWARD_TO}\n{FORWARD_TO}"})

    competition.refresh_from_db()
    assert competition.forward_emails == [FORWARD_TO]


# --- walidacja ------------------------------------------------------------------------------------


def test_a_malformed_address_is_refused_under_the_field(coordinator_client, competition):
    response = coordinator_client.post(URL, {"submission_forward_emails": "komitet(at)example.invalid"})

    competition.refresh_from_db()
    assert response.status_code == 400
    assert "To nie są poprawne adresy e-mail" in response.content.decode()
    assert competition.submission_forward_emails == ""
    assert not audit_entries().exists()


def test_more_than_the_limit_is_refused(coordinator_client, competition):
    too_many = "\n".join(f"komitet{index}@example.invalid" for index in range(MAX_FORWARD_EMAILS + 1))

    response = coordinator_client.post(URL, {"submission_forward_emails": too_many})

    competition.refresh_from_db()
    assert response.status_code == 400
    assert f"najwyżej {MAX_FORWARD_EMAILS}" in response.content.decode()
    assert competition.submission_forward_emails == ""


def test_a_refused_save_still_shows_the_addresses_that_apply(coordinator_client, competition):
    """Po odrzuceniu ramka „dziś przekazujemy” ma pokazywać stan z bazy, a nie odrzuconą próbę."""
    competition.submission_forward_emails = FORWARD_TO
    competition.save(update_fields=["submission_forward_emails"])

    response = coordinator_client.post(URL, {"submission_forward_emails": "bez-malpy"})

    assert response.status_code == 400
    assert FORWARD_TO in response.content.decode()
    competition.refresh_from_db()
    assert competition.forward_emails == [FORWARD_TO]


# --- uprawnienia i zakres konkursu ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_open_the_screen(web_client, participant, reviewer, role):
    web_client.force_login(participant.user if role == "participant" else reviewer.user)

    assert web_client.get(URL).status_code == 403
    assert web_client.post(URL, {"submission_forward_emails": FORWARD_TO}).status_code == 403


def test_anonymous_is_redirected_to_login(web_client):
    response = web_client.get(URL)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login/")


def test_the_save_touches_only_the_competition_of_the_request(client_for, competition, other_competition):
    """Konkurs bierze się z domeny żądania – ten sam adres pod drugą domeną opisuje drugi konkurs."""
    user = CoordinatorFactory()
    grant_membership(user, other_competition, CompetitionRole.COORDINATOR)
    client = client_for(other_competition)
    client.force_login(user)

    client.post(URL, {"submission_forward_emails": "sasiad@example.invalid"})

    competition.refresh_from_db()
    other_competition.refresh_from_db()
    assert other_competition.forward_emails == ["sasiad@example.invalid"]
    assert competition.forward_emails == []


def test_the_menu_links_to_the_screen(coordinator_client):
    content = coordinator_client.get("/coordinator/").content.decode()

    assert f'href="{URL}"' in content
    assert "Przekazywanie rozwiązań" in content
