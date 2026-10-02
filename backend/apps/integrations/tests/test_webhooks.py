"""Webhooki: emisja przy zdarzeniach domenowych, podpis, ponowienia i wygaszanie odbiorcy.

Żądań HTTP nie wysyłamy naprawdę – biblioteki ``responses``/``requests-mock`` nie ma w obrazie,
więc podstawiamy ``requests.post`` przez ``unittest.mock``. To i tak jest właściwy poziom: badamy
**naszą** maszynę stanów doręczenia, a nie to, czy ``requests`` umie wysłać POST.
"""

import hashlib
import hmac
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.core.api import DomainError
from apps.integrations.models import (
    EVENT_RESULTS_PUBLISHED,
    EVENT_STAGE_CLOSED,
    MAX_CONSECUTIVE_FAILURES,
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.integrations.services import (
    TEST_EVENT,
    create_endpoint,
    resend_delivery,
    send_test_delivery,
    update_endpoint,
)
from apps.integrations.webhooks import (
    MAX_ATTEMPTS,
    attempt_delivery,
    canonical_body,
    emit,
    retry_countdown,
    signature_header,
)

pytestmark = pytest.mark.django_db


class FakeResponse:
    """Odpowiedź serwera partnera – tyle, ile czyta ``post_payload`` (sam kod stanu)."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code


def _post(status_code: int = 200):
    return patch("requests.post", return_value=FakeResponse(status_code))


# --- podpis ---------------------------------------------------------------------------------------


def test_signature_matches_manual_verification():
    """Podpis daje się sprawdzić dokładnie tak, jak opisuje ``docs/API.md``."""
    body = canonical_body({"id": 7, "event": EVENT_STAGE_CLOSED, "created_at": "x", "data": {}})
    header = signature_header("tajny-sekret", body, 1_700_000_000)

    timestamp, signature = (part.split("=", 1)[1] for part in header.split(","))
    expected = hmac.new(b"tajny-sekret", f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    assert signature == expected
    assert timestamp == "1700000000"


def test_canonical_body_is_stable():
    """Ta sama treść daje ten sam bajt w bajt ładunek – inaczej ponowienie zmieniałoby podpis."""
    first = canonical_body({"b": 1, "a": {"y": 2, "x": 1}})
    second = canonical_body({"a": {"x": 1, "y": 2}, "b": 1})
    assert first == second


# --- emisja ---------------------------------------------------------------------------------------


def test_emit_creates_one_delivery_per_subscriber(endpoint, edition, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False):
        ids = emit(EVENT_STAGE_CLOSED, {"stage_id": 1}, edition=edition)

    assert len(ids) == 1
    delivery = WebhookDelivery.objects.get(pk=ids[0])
    assert delivery.endpoint == endpoint
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.payload == {"stage_id": 1}


def test_emit_skips_unsubscribed_inactive_and_foreign_editions(coordinator, edition):
    from apps.competitions.tests.factories import EditionFactory

    subscribed = create_endpoint(
        url="https://a.example.test/hook", events=[EVENT_STAGE_CLOSED], edition=edition
    )
    create_endpoint(url="https://b.example.test/hook", events=[EVENT_RESULTS_PUBLISHED], edition=edition)
    create_endpoint(url="https://c.example.test/hook", events=[EVENT_STAGE_CLOSED], edition=EditionFactory())
    disabled = create_endpoint(url="https://d.example.test/hook", events=[EVENT_STAGE_CLOSED])
    update_endpoint(disabled, is_active=False, actor=coordinator)

    ids = emit(EVENT_STAGE_CLOSED, {"stage_id": 1}, edition=edition)

    assert [WebhookDelivery.objects.get(pk=pk).endpoint_id for pk in ids] == [subscribed.pk]


def test_emit_refuses_unknown_event(edition):
    with pytest.raises(ValueError):
        emit("nie.ma.takiego", {}, edition=edition)


def test_https_is_required():
    with pytest.raises(DomainError) as exc:
        create_endpoint(url="http://partner.example.test/hook", events=[EVENT_STAGE_CLOSED])
    assert exc.value.machine_code == "INVALID_WEBHOOK"


def test_unknown_event_is_refused_at_creation():
    with pytest.raises(DomainError) as exc:
        create_endpoint(url="https://partner.example.test/hook", events=["results.leaked"])
    assert exc.value.machine_code == "INVALID_EVENTS"


def test_endpoint_model_validates_events_directly():
    endpoint = WebhookEndpoint(url="https://x.example.test/h", events=["nope"])
    with pytest.raises(ValidationError):
        endpoint.clean()


# --- doręczenie ------------------------------------------------------------------------------------


def test_successful_delivery_marks_row_and_resets_failures(endpoint, edition):
    endpoint.failures = 3
    endpoint.save(update_fields=["failures"])
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with _post(202) as mocked:
        assert attempt_delivery(delivery) is True

    delivery.refresh_from_db()
    endpoint.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.delivered_at is not None
    assert delivery.attempts == 1
    assert endpoint.failures == 0
    headers = mocked.call_args.kwargs["headers"]
    assert headers["X-Olimpiada-Event"] == EVENT_STAGE_CLOSED
    assert headers["X-Olimpiada-Delivery"] == str(delivery.pk)
    assert headers["X-Olimpiada-Signature"].startswith("t=")
    assert mocked.call_args.kwargs["timeout"] == 10


def test_failed_delivery_stays_pending_until_attempts_run_out(endpoint):
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    for attempt in range(1, MAX_ATTEMPTS):
        with _post(500):
            assert attempt_delivery(delivery) is False
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == attempt
        assert delivery.last_error == "HTTP 500"

    with _post(500):
        attempt_delivery(delivery)
    delivery.refresh_from_db()
    endpoint.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert endpoint.failures == 1


def test_network_error_is_recorded_not_raised(endpoint):
    import requests

    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})
    with patch("requests.post", side_effect=requests.ConnectionError("brak trasy")):
        assert attempt_delivery(delivery) is False

    delivery.refresh_from_db()
    assert delivery.last_error == "ConnectionError"
    # Pakiet 5: treść wyjątku (host, port, powód odmowy) nie trafia do panelu.
    assert "brak trasy" not in delivery.last_error


# --- SSRF: dokąd worker nie może wysłać żądania (pakiet 5 po audycie) -----------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/hook",
        "https://10.0.0.5/hook",
        "https://172.30.1.4:8000/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/hook",
        "https://[::ffff:10.0.0.1]/hook",
        "https://[fd00::1]/hook",
        "https://db/hook",
        "https://minio:9000/hook",
        "https://web:8000/internal/",
        "https://localhost/hook",
        "https://api.localhost/hook",
    ],
)
def test_internal_targets_are_refused_when_the_endpoint_is_saved(url):
    with pytest.raises(DomainError) as exc:
        create_endpoint(url=url, events=[EVENT_STAGE_CLOSED])
    assert exc.value.machine_code == "INVALID_WEBHOOK"
    assert not WebhookEndpoint.objects.exists()


def test_a_public_ip_literal_is_still_accepted():
    create_endpoint(url="https://93.184.215.14/hook", events=[EVENT_STAGE_CLOSED])


@pytest.mark.parametrize(
    "resolved",
    [
        ["10.1.2.3"],
        ["172.30.2.10"],
        ["93.184.215.14", "127.0.0.1"],
        ["93.184.215.14", "fe80::1"],
        ["169.254.169.254"],
    ],
)
def test_delivery_is_refused_when_any_resolved_address_is_internal(endpoint, monkeypatch, resolved):
    """Nazwa „publiczna” rozwiązana na adres wewnętrzny – żądanie w ogóle nie wychodzi."""
    import ipaddress

    from apps.integrations.targets import REFUSED_ADDRESS

    monkeypatch.setattr(
        "apps.integrations.targets.resolve", lambda host, port: [ipaddress.ip_address(a) for a in resolved]
    )
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with patch("requests.post") as mocked:
        assert attempt_delivery(delivery) is False
        assert not mocked.called

    delivery.refresh_from_db()
    assert delivery.last_error == REFUSED_ADDRESS
    assert endpoint.url not in delivery.last_error


def test_endpoint_saved_before_the_rule_is_refused_at_delivery(endpoint):
    """Wiersz zapisany z pominięciem ``full_clean`` (np. sprzed tej zmiany) nie omija reguły."""
    WebhookEndpoint.objects.filter(pk=endpoint.pk).update(url="https://redis:6379/")
    endpoint.refresh_from_db()
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with patch("requests.post") as mocked:
        assert attempt_delivery(delivery) is False
        assert not mocked.called


def test_unresolvable_name_is_a_generic_failure(endpoint, monkeypatch):
    import socket

    from apps.integrations.targets import UNRESOLVED_ADDRESS

    def no_such_host(host, port):
        raise socket.gaierror("Name or service not known: partner.example.test")

    monkeypatch.setattr("apps.integrations.targets.resolve", no_such_host)
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with patch("requests.post") as mocked:
        attempt_delivery(delivery)
        assert not mocked.called

    delivery.refresh_from_db()
    assert delivery.last_error == UNRESOLVED_ADDRESS


def test_redirects_are_not_followed(endpoint):
    """3xx to porażka doręczenia, a nie zaproszenie pod niesprawdzony adres."""
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with _post(302) as mocked:
        assert attempt_delivery(delivery) is False

    assert mocked.call_args.kwargs["allow_redirects"] is False
    delivery.refresh_from_db()
    assert delivery.last_error == "HTTP 302"


def test_endpoint_is_disabled_after_too_many_failures(endpoint):
    endpoint.failures = MAX_CONSECUTIVE_FAILURES - 1
    endpoint.save(update_fields=["failures"])
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={}, attempts=MAX_ATTEMPTS - 1
    )

    with _post(500):
        attempt_delivery(delivery)

    endpoint.refresh_from_db()
    assert endpoint.is_active is False
    assert endpoint.disabled_at is not None


def test_reenabling_endpoint_clears_the_counter(endpoint, coordinator):
    endpoint.is_active = False
    endpoint.failures = MAX_CONSECUTIVE_FAILURES
    endpoint.save(update_fields=["is_active", "failures"])

    update_endpoint(endpoint, is_active=True, actor=coordinator)

    endpoint.refresh_from_db()
    assert endpoint.failures == 0
    assert endpoint.disabled_at is None


def test_retry_countdown_grows_and_is_capped():
    assert retry_countdown(1) == 60
    assert retry_countdown(2) == 120
    assert retry_countdown(50) == 3600


# --- panel: test i ponowienie ----------------------------------------------------------------------


def test_test_delivery_uses_its_own_event_name(endpoint, coordinator, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False):
        delivery = send_test_delivery(endpoint, actor=coordinator)

    assert delivery.event == TEST_EVENT
    assert delivery.status == DeliveryStatus.PENDING


def test_resend_reuses_the_same_row(endpoint, coordinator, django_capture_on_commit_callbacks):
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event=EVENT_STAGE_CLOSED,
        payload={"stage_id": 1},
        status=DeliveryStatus.FAILED,
        attempts=MAX_ATTEMPTS,
        last_error="HTTP 500",
    )

    with django_capture_on_commit_callbacks(execute=False):
        resend_delivery(delivery, actor=coordinator)

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempts == 0
    assert delivery.last_error == ""
    assert WebhookDelivery.objects.count() == 1


def test_resend_refuses_disabled_endpoint(endpoint, coordinator):
    endpoint.is_active = False
    endpoint.save(update_fields=["is_active"])
    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})

    with pytest.raises(DomainError) as exc:
        resend_delivery(delivery, actor=coordinator)
    assert exc.value.machine_code == "ENDPOINT_DISABLED"


# --- zadanie Celery --------------------------------------------------------------------------------


def test_task_delivers_and_is_idempotent(endpoint):
    from apps.integrations.tasks import deliver_webhook

    delivery = WebhookDelivery.objects.create(endpoint=endpoint, event=EVENT_STAGE_CLOSED, payload={})
    with _post(200):
        assert deliver_webhook(delivery.pk) == DeliveryStatus.DELIVERED
    # Powtórne wykonanie tego samego zadania nie wysyła drugi raz.
    with patch("requests.post") as mocked:
        assert deliver_webhook(delivery.pk) == DeliveryStatus.DELIVERED
        assert not mocked.called


def test_task_survives_missing_row():
    from apps.integrations.tasks import deliver_webhook

    assert deliver_webhook(9_999_999) == "MISSING"
