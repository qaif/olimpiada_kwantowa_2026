"""Ekran integracji w panelu koordynatora i eksporty pod ``/coordinator/export/…``."""

import json
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import DEFAULT_PASSWORD, ParticipantFactory, UserFactory
from apps.integrations.models import (
    EVENT_STAGE_CLOSED,
    SCOPE_READ_RESULTS,
    ApiKey,
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.results.tests.conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

PANEL = "/coordinator/integrations/"


@pytest.fixture
def panel(coordinator) -> Client:
    client = Client()
    client.force_login(coordinator)
    return client


def test_panel_requires_coordinator(coordinator):
    anonymous = Client()
    assert anonymous.get(PANEL).status_code == 302

    participant = ParticipantFactory()
    logged = Client()
    logged.force_login(participant.user)
    assert logged.get(PANEL).status_code == 403


def test_panel_lists_keys_endpoints_and_deliveries(panel, make_key, endpoint):
    key, _ = make_key(name="Kuratorium Mazowieckie")
    WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    response = panel.get(PANEL)
    body = response.content.decode()

    assert response.status_code == 200
    assert "Kuratorium Mazowieckie" in body
    assert key.prefix in body
    assert endpoint.url in body
    assert EVENT_STAGE_CLOSED in body


def test_creating_key_shows_it_exactly_once(panel):
    response = panel.post(
        reverse("web:coordinator-integrations-key-create"),
        {"name": "Uczelnia", "scopes": [SCOPE_READ_RESULTS], "rate_limit_per_minute": 60},
        follow=True,
    )
    body = response.content.decode()
    key = ApiKey.objects.get(name="Uczelnia")

    assert response.status_code == 200
    assert f"ok_{key.prefix}_" in body
    # Drugie wejście na ekran klucza już nie pokazuje – postać jawna zniknęła z sesji.
    assert f"ok_{key.prefix}_" not in panel.get(PANEL).content.decode()


def test_creating_key_without_scopes_is_rejected(panel):
    response = panel.post(
        reverse("web:coordinator-integrations-key-create"),
        {"name": "Pusty", "rate_limit_per_minute": 60},
    )

    assert response.status_code == 400
    assert not ApiKey.objects.filter(name="Pusty").exists()


def test_revoking_key_from_panel(panel, make_key):
    key, _ = make_key()
    response = panel.post(reverse("web:coordinator-integrations-key-revoke", args=[key.pk]))

    key.refresh_from_db()
    assert response.status_code == 302
    assert key.revoked_at is not None


def test_webhook_crud_from_panel(panel):
    created = panel.post(
        reverse("web:coordinator-integrations-webhook-create"),
        {"url": "https://partner.example.test/hook", "events": [EVENT_STAGE_CLOSED]},
    )
    endpoint = WebhookEndpoint.objects.get()
    assert created.status_code == 302
    assert endpoint.events == [EVENT_STAGE_CLOSED]
    assert endpoint.secret

    panel.post(
        reverse("web:coordinator-integrations-webhook-update", args=[endpoint.pk]),
        {"url": "https://partner.example.test/inny", "events": [EVENT_STAGE_CLOSED]},
    )
    endpoint.refresh_from_db()
    assert endpoint.url == "https://partner.example.test/inny"

    panel.post(reverse("web:coordinator-integrations-webhook-delete", args=[endpoint.pk]))
    assert not WebhookEndpoint.objects.exists()


def test_http_endpoint_is_rejected_with_a_message(panel):
    response = panel.post(
        reverse("web:coordinator-integrations-webhook-create"),
        {"url": "http://partner.example.test/hook", "events": [EVENT_STAGE_CLOSED]},
        follow=True,
    )

    assert not WebhookEndpoint.objects.exists()
    assert "https" in response.content.decode()


def test_test_delivery_and_resend_from_panel(panel, endpoint, django_capture_on_commit_callbacks):
    """Doręczenie próbne idzie tą samą drogą, co prawdziwe – łącznie z kolejką po commicie."""
    with patch("requests.post") as mocked:
        mocked.return_value.status_code = 200
        with django_capture_on_commit_callbacks(execute=True):
            panel.post(reverse("web:coordinator-integrations-webhook-test", args=[endpoint.pk]))

    delivery = WebhookDelivery.objects.get()
    assert delivery.event == "ping"
    assert delivery.status == DeliveryStatus.DELIVERED

    with patch("requests.post") as mocked:
        mocked.return_value.status_code = 500
        with django_capture_on_commit_callbacks(execute=True):
            panel.post(reverse("web:coordinator-integrations-delivery-resend", args=[delivery.pk]))

    delivery.refresh_from_db()
    assert delivery.attempts >= 1
    assert delivery.last_error == "HTTP 500"


# --- eksporty ------------------------------------------------------------------------------------


@pytest.fixture
def scored_stage(edition):
    stage = make_stage(edition=edition, min_points=6)
    graded_entry(
        stage,
        [6, 5],
        participant=ParticipantFactory(
            user=UserFactory(first_name="Łucja", last_name="Śniadecka", password=DEFAULT_PASSWORD),
            school="XIV LO Warszawa",
            district="mazowieckie",
            grade=3,
        ),
    )
    return stage


def test_kuratorium_export_requires_voivodeship(panel, scored_stage):
    url = reverse("web:coordinator-export-kuratorium", args=["csv"])
    assert panel.get(url, {"stage": scored_stage.pk}).status_code == 404
    assert panel.get(url, {"stage": scored_stage.pk, "voivodeship": "marsjanskie"}).status_code == 404


def test_kuratorium_export_returns_csv(panel, scored_stage):
    response = panel.get(
        reverse("web:coordinator-export-kuratorium", args=["csv"]),
        {"stage": scored_stage.pk, "voivodeship": "mazowieckie"},
    )
    body = b"".join(response.streaming_content).decode("utf-8")

    assert response.status_code == 200
    assert "kod publiczny" in body
    assert "Śniadecka" in body
    assert "zakwalifikowany" in body


def test_protocol_export_returns_pdf(panel, scored_stage):
    response = panel.get(reverse("web:coordinator-export-protocol"), {"stage": scored_stage.pk})

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "protokol-etap" in response["Content-Disposition"]


def test_edition_json_export(panel, scored_stage, edition):
    response = panel.get(reverse("web:coordinator-export-edition-json"), {"edition": edition.pk})
    payload = json.loads(response.content.decode("utf-8"))

    assert response.status_code == 200
    assert payload["edition"]["id"] == edition.pk
    assert "Śniadecka" not in response.content.decode("utf-8")


def test_exports_are_closed_to_other_roles(scored_stage):
    participant = ParticipantFactory()
    client = Client()
    client.force_login(participant.user)

    response = client.get(reverse("web:coordinator-export-protocol"), {"stage": scored_stage.pk})
    assert response.status_code == 403


def test_export_page_links_to_new_exports(panel, scored_stage):
    """Nowe eksporty mają być **widoczne** tam, gdzie koordynator szuka plików."""
    body = panel.get(reverse("web:coordinator-export")).content.decode()

    assert reverse("web:coordinator-export-protocol") in body
    assert reverse("web:coordinator-export-edition-json") in body
    assert "kuratorium" in body
