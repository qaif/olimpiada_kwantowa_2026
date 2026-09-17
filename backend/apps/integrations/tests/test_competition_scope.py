"""Wydanie D: poświadczenie partnera należy do konkursu, a nie do instalacji.

Dwa miejsca ważą tu więcej niż reszta:

- **klucz API konkursu A pod domeną konkursu B** dostaje 404, a nie 401 ani 403. To jest ta sama
  reguła, którą ``/api/v1/`` stosuje już do edycji spoza zakresu klucza: istnienie konkursu B nie
  jest informacją partnera konkursu A, więc odpowiedź brzmi „nie ma tego tutaj”. 403 („jesteś, ale
  nie tobie”) potwierdzałoby istnienie i pozwalało przejechać listę domen instalacji jednym
  kluczem,
- **emisja webhooka** idzie wyłącznie do odbiorców swojego konkursu. Odbiorca „bez edycji” znaczy
  „wszystkie edycje **jego** konkursu”, a nie wszystkie edycje instalacji – bez tego partner
  organizatora A dostawałby na swój serwer zdarzenia organizatora B razem z kodami jego
  uczestników. To jest jedyne miejsce w tym etapie, w którym wyciek wychodziłby **poza** serwis.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.competitions.tests.factories import CurrentEditionFactory
from apps.competitions.tests.scope_helpers import host_of
from apps.integrations.models import (
    EVENT_STAGE_CLOSED,
    WEBHOOK_EVENTS,
    ApiKey,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.integrations.services import (
    api_keys_for_panel,
    create_api_key,
    create_endpoint,
    deliveries_for_panel,
)
from apps.integrations.webhooks import emit

from .conftest import WEBHOOK_URL

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_with_key(settings):
    """``client_with_key(konkurs_hosta, token)`` – klient ``/api/v1/`` pod domeną konkursu."""
    from conftest import allow_test_hosts

    def make(competition, token: str) -> APIClient:
        allow_test_hosts(settings)
        host = host_of(competition)
        client = APIClient(HTTP_HOST=host, SERVER_NAME=host)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return make


def make_key(competition, coordinator=None):
    return create_api_key(
        name="Partner testowy",
        scopes=["read:results"],
        competition=competition,
        actor=coordinator,
    )


# --- zakresowanie zapytań ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "path"),
    [
        (ApiKey, "competition"),
        (WebhookEndpoint, "competition"),
        # Bez własnej kolumny: doręczenie dochodzi do konkursu przez swojego odbiorcę (§ 3.1).
        (WebhookDelivery, "endpoint__competition"),
    ],
)
def test_every_model_declares_its_way_to_the_competition(model, path):
    assert model.objects.all().competition_path == path


def test_keys_and_endpoints_of_another_competition_are_invisible(competition, other_competition):
    key_b, _token = make_key(other_competition)
    endpoint_b = create_endpoint(url=WEBHOOK_URL, events=[EVENT_STAGE_CLOSED], competition=other_competition)

    assert api_keys_for_panel(competition=competition) == []
    assert api_keys_for_panel(competition=other_competition) == [key_b]
    assert list(WebhookEndpoint.objects.for_competition(competition)) == []
    assert list(WebhookEndpoint.objects.for_competition(other_competition)) == [endpoint_b]


def test_delivery_log_is_scoped_through_its_endpoint(competition, other_competition):
    endpoint_b = create_endpoint(url=WEBHOOK_URL, events=[EVENT_STAGE_CLOSED], competition=other_competition)
    delivery_b = WebhookDelivery.objects.create(endpoint=endpoint_b, event=EVENT_STAGE_CLOSED)

    assert deliveries_for_panel(competition=competition) == []
    assert deliveries_for_panel(competition=other_competition) == [delivery_b]


def test_a_key_cannot_be_pinned_to_an_edition_of_another_competition(competition, other_competition):
    """Klucz konkursu A na edycji konkursu B miałby dwa zakresy danych naraz – czyli żaden."""
    from apps.core.api import DomainError

    edition_b = CurrentEditionFactory(competition=other_competition)

    with pytest.raises(DomainError):
        create_api_key(
            name="Partner testowy",
            scopes=["read:results"],
            competition=competition,
            edition=edition_b,
        )


# --- uwierzytelnienie ----------------------------------------------------------------------------


def test_a_key_of_another_competition_is_not_found_here(client_with_key, competition, other_competition):
    """Klucz konkursu B pod domeną konkursu A: 404, nie 401 i nie 403 (§ 3, reguła odpowiedzi)."""
    _key, token = make_key(other_competition)

    foreign = client_with_key(competition, token).get("/api/v1/")
    home = client_with_key(other_competition, token).get("/api/v1/")

    assert foreign.status_code == 404
    assert foreign.json()["code"] == "NOT_FOUND"
    assert home.status_code == 200
    assert home.json()["key_prefix"] == _key.prefix


def test_stages_of_another_competition_are_not_found_for_a_key(
    client_with_key, competition, other_competition
):
    """Nawet pod własną domeną klucz nie sięga po etap cudzego konkursu – zawęża go queryset."""
    from apps.competitions.tests.factories import StageFactory

    _key, token = make_key(competition)
    stage_b = StageFactory(competition=other_competition)

    response = client_with_key(competition, token).get(f"/api/v1/stages/{stage_b.pk}/results/")

    assert response.status_code == 404


# --- emisja --------------------------------------------------------------------------------------


def test_events_reach_only_the_endpoints_of_their_competition(competition, other_competition):
    """Odbiorca „bez edycji” dostaje wszystkie edycje **swojego** konkursu, a nie instalacji."""
    ours = create_endpoint(url=WEBHOOK_URL, events=list(WEBHOOK_EVENTS), competition=competition)
    theirs = create_endpoint(
        url="https://obcy.example.test/hooks", events=list(WEBHOOK_EVENTS), competition=other_competition
    )
    edition_a = CurrentEditionFactory(competition=competition)

    delivery_ids = emit(EVENT_STAGE_CLOSED, {"stage_id": 1}, edition=edition_a)

    endpoints = set(WebhookDelivery.objects.filter(pk__in=delivery_ids).values_list("endpoint_id", flat=True))
    assert endpoints == {ours.pk}
    assert theirs.pk not in endpoints
