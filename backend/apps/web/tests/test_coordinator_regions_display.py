"""Nazwa regionu zamiast województwa na istniejących ekranach panelu (etap 2, T20, § 1.4).

Trzy ekrany, które dotąd pokazywały województwo, mają przy włączonej fladze ``custom_regions``
pokazywać nazwę regionu: karta uczestnika, karta członka komitetu i lista komitetu. Reguła jest
jednostronna i to jest cały przedmiot tego pliku:

- **przy wyłączonej fladze nie zmienia się nic** – ani napis, ani liczba zapytań. Konkurs #1 ma
  mieć te strony co do znaku takie, jak przed etapem 2 (§ 0.1),
- **przy włączonej** nazwa regionu zastępuje etykietę województwa, a profil bez regionu (założony
  przed włączeniem flagi) dalej pokazuje województwo, zamiast pokazywać pustkę,
- flagę czyta **widok**, nie szablon (§ 2.1) – dlatego asercje sięgają po zmienną kontekstu,
  a nie po sam napis na stronie.

Adresy są produkcyjne: te trzy ekrany istnieją od dawna i tego zadania nie dotyczy ich montaż.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import CompetitionRole, Region
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

FEATURE = "custom_regions"


def enable(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def region(competition, code: str) -> Region:
    return Region.objects.for_competition(competition).get(code=code)


def card_url(participant) -> str:
    return reverse("web:coordinator-participant", args=[participant.pk])


def member_url(member) -> str:
    return reverse("web:coordinator-member", args=[member.pk])


# --- karta uczestnika -----------------------------------------------------------------------------


def test_the_participant_card_shows_the_voivodeship_without_the_flag(coordinator_client, competition):
    participant = ParticipantFactory(region=region(competition, "mazowieckie"))

    response = coordinator_client.get(card_url(participant))

    assert response.status_code == 200
    # Pusty napis znaczy „czytaj województwo” – i tak robi szablon.
    assert response.context["region_label"] == ""
    assert "Województwo" in response.content.decode()


def test_the_participant_card_shows_the_region_with_the_flag(coordinator_client, competition):
    enable(competition)
    row = region(competition, "mazowieckie")
    row.name = "Okręg mazowiecki"
    row.save(update_fields=["name"])
    participant = ParticipantFactory(region=row)

    response = coordinator_client.get(card_url(participant))

    page = response.content.decode()
    assert response.context["region_label"] == "Okręg mazowiecki"
    assert "Okręg mazowiecki" in page
    assert "<dt>Województwo</dt>" not in page


def test_a_participant_without_a_region_keeps_the_voivodeship(coordinator_client, competition):
    enable(competition)
    participant = ParticipantFactory(region=None)

    response = coordinator_client.get(card_url(participant))

    assert response.context["region_label"] == ""
    assert "Województwo" in response.content.decode()


def test_the_region_costs_the_card_no_extra_query(coordinator_client, competition):
    participant = ParticipantFactory(region=region(competition, "mazowieckie"))
    coordinator_client.get(card_url(participant))  # rozgrzewka liczników menu

    with CaptureQueriesContext(connection) as without_flag:
        coordinator_client.get(card_url(participant))

    enable(competition)

    with CaptureQueriesContext(connection) as with_flag:
        coordinator_client.get(card_url(participant))

    # ``select_related("region")`` zamiast odczytu relacji: koszt jest ten sam co bez flagi.
    assert len(with_flag) == len(without_flag)


# --- komitet --------------------------------------------------------------------------------------


def test_the_member_card_shows_the_region_with_the_flag(coordinator_client, competition):
    enable(competition)
    row = region(competition, "pomorskie")
    member = ActiveReviewerFactory(region=row, district="pomorskie")

    response = coordinator_client.get(member_url(member))

    assert response.context["region_label"] == row.name
    assert row.name in response.content.decode()


def test_the_member_card_keeps_the_voivodeship_without_the_flag(coordinator_client, competition):
    member = ActiveReviewerFactory(region=region(competition, "pomorskie"), district="pomorskie")

    response = coordinator_client.get(member_url(member))

    assert response.context["region_label"] == ""


def test_the_member_list_shows_the_region_with_the_flag(coordinator_client, competition):
    enable(competition)
    row = region(competition, "pomorskie")
    row.name = "Okręg pomorski"
    row.save(update_fields=["name"])
    ActiveReviewerFactory(region=row, district="pomorskie")

    response = coordinator_client.get(reverse("web:coordinator-members"))

    labels = [item.get("region_label") for item in response.context["rows"]]
    assert labels == ["Okręg pomorski"]
    assert "Okręg pomorski" in response.content.decode()


def test_the_member_list_without_the_flag_has_no_region_key_at_all(coordinator_client, competition):
    ActiveReviewerFactory(region=region(competition, "pomorskie"), district="pomorskie")

    response = coordinator_client.get(reverse("web:coordinator-members"))

    rows = response.context["rows"]
    assert rows
    assert all("region_label" not in row for row in rows)
    assert "pomorskie" in response.content.decode()
