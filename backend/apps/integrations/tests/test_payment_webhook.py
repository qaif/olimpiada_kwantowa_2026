"""Webhook płatności – pierwszy w repozytorium adres przyjmujący ruch **do nas** (§ 1.5.1, T49).

Ten plik pilnuje pięciu rzeczy i każda z nich jest osobnym rodzajem szkody, gdyby przestała
obowiązywać:

- **Konkurs #1 tego adresu nie ma.** Flaga ``fees`` jest wyłączona, więc odpowiedzią jest 404,
  a nie 403 i nie 401 – wpisowe jest zdolnością dołożoną, a Olimpiada Kwantowa jest bezpłatna
  (§ 0.1, § 3).
- **Bez poprawnego podpisu nie dzieje się nic.** Zły podpis, brak nagłówka i podpis złożony poza
  oknem tolerancji dostają 401 i **nie zostawiają wiersza** – inaczej sam dziennik doręczeń byłby
  drogą do zapełnienia bazy cudzym ruchem.
- **Powtórzone doręczenie jest powtórzeniem, a nie drugą wpłatą.** Dostawca ponawia po każdym
  timeoucie; druga data wpłaty na tej samej należności znaczyłaby, że rejestr odpowiada „kiedy”
  inaczej za każdym razem.
- **Nieznana należność kończy się 2xx.** Potwierdzenie, którego nie da się dopasować, ma zostać
  zapisane i oddane organizatorowi do ręki, a nie ponawiane przez dostawcę w nieskończoność.
- **Sekret jest per konkurs.** Poprawnie podpisane doręczenie konkursu B pod domeną konkursu A
  jest niczym – tak samo, jak klucz API konkursu B pod tą domeną (``test_competition_scope``).
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.tests.factories import EditionFactory
from apps.core.models import AuditLog
from apps.integrations.inbound import (
    PAID_STATUS,
    TOLERANCE_SECONDS,
    create_payment_endpoint,
    parse_signature,
    payment_endpoints_for,
    payment_events_for,
    rotate_payment_secret,
    verify_signature,
)
from apps.integrations.models import (
    UNMATCHED_FEE_NOT_DUE,
    UNMATCHED_IGNORED_STATUS,
    UNMATCHED_UNKNOWN_REFERENCE,
    PaymentEndpoint,
    PaymentEvent,
)
from apps.integrations.webhooks import SIGNATURE_HEADER, signature_header
from apps.tenancy.fees import FeeStatus, ParticipantFee, assign_fee, create_fee_schedule

pytestmark = pytest.mark.django_db

#: Slug dostawcy. Celowo bez związku z jakimkolwiek prawdziwym operatorem: dostawca nie jest
#: wybrany (decyzja D18), a test, który nazywa go po imieniu, zaczyna opisywać cudze API.
PROVIDER = "dostawca-testowy"

#: Identyfikator wpłaty, który organizator przypisuje należności, zakładając płatność u dostawcy.
REFERENCE = "OLM-WPIS-000123"

AMOUNT = Decimal("49.99")


def path(provider: str = PROVIDER) -> str:
    return f"/api/v1/payments/{provider}/"


def body_of(**fields) -> bytes:
    """Ciało żądania w postaci bajtów – takiej, w jakiej idzie na podpis.

    Bajty, a nie słownik: podpis obejmuje **bajty**, więc test, który dałby klientowi obiekt do
    zserializowania, podpisywałby co innego, niż wysyła, i przechodziłby albo padał zależnie od
    kolejności kluczy w słowniku (``docs/API.md`` § 3.3).
    """
    payload = {"reference": REFERENCE, **fields}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def signed(client: APIClient, endpoint: PaymentEndpoint, body: bytes, *, timestamp: int | None = None):
    """Doręczenie podpisane sekretem tego webhooka."""
    stamp = timestamp if timestamp is not None else int(timezone.now().timestamp())
    return client.post(
        path(endpoint.provider),
        data=body,
        content_type="application/json",
        **{
            f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}": signature_header(
                endpoint.secret, body, stamp
            )
        },
    )


@pytest.fixture
def paying(other_competition, settings):
    """Konkurs #2 z wpisowem: flaga, cennik, uczestnik i jedna należność z identyfikatorem wpłaty.

    Identyfikator wpłaty stawia tu **organizator**, zakładając płatność u dostawcy – rejestr sam
    go nie wymyśla, bo to dostawca decyduje, co wróci w potwierdzeniu. Dopasowanie webhooka stoi
    dokładnie na tym jednym polu.
    """
    from conftest import allow_test_hosts

    allow_test_hosts(settings)
    other_competition.feature_flags = {**(other_competition.feature_flags or {}), "fees": True}
    other_competition.save(update_fields=["feature_flags"])
    edition = EditionFactory(competition=other_competition)
    create_fee_schedule(other_competition, edition=edition, name="Wpisowe", amount=AMOUNT)
    participant = ParticipantFactory(competition=other_competition)
    fee = assign_fee(participant, edition)
    ParticipantFee.objects.filter(pk=fee.pk).update(external_reference=REFERENCE)
    fee.refresh_from_db()
    endpoint = create_payment_endpoint(other_competition, provider=PROVIDER)
    host = other_competition.primary_domain
    client = APIClient(HTTP_HOST=host, SERVER_NAME=host)
    return client, endpoint, fee


# --- podpis: jednostkowo ---------------------------------------------------------------------------


def test_signature_is_symmetric_with_the_outgoing_one():
    """Ten sam nagłówek, ta sama formuła, ta sama tolerancja – po obu stronach jedna funkcja."""
    body = b'{"reference":"X"}'
    stamp = int(timezone.now().timestamp())
    header = signature_header("sekret", body, stamp)
    assert parse_signature(header) == (stamp, header.split("v1=")[1])
    assert verify_signature("sekret", body, header) is True


@pytest.mark.parametrize(
    "header",
    ["", "v1=abc", "t=nie-liczba,v1=abc", "t=123", "t=123,v1=", "kompletnie nie ten kształt"],
)
def test_malformed_signature_header_is_rejected(header):
    """Tolerancja dla odstępów i kolejności kończy się tam, gdzie zaczyna się zgadywanie."""
    assert verify_signature("sekret", b"{}", header) is False


def test_signature_of_another_body_does_not_verify():
    """Podpis jest podpisem **tych** bajtów: jeden znak różnicy unieważnia go w całości."""
    stamp = int(timezone.now().timestamp())
    header = signature_header("sekret", b'{"reference":"A"}', stamp)
    assert verify_signature("sekret", b'{"reference":"B"}', header) is False


# --- Konkurs #1: tego adresu nie ma -----------------------------------------------------------------


def test_competition_one_gets_404(competition, settings):
    """Bez flagi ``fees`` adres nie istnieje – 404, a nie 403 (§ 3) i bez śladu w dzienniku."""
    from conftest import allow_test_hosts

    allow_test_hosts(settings)
    host = competition.primary_domain
    client = APIClient(HTTP_HOST=host, SERVER_NAME=host)
    response = client.post(path(), data=body_of(), content_type="application/json")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert not PaymentEvent.objects.exists()


def test_unknown_provider_gets_404(paying):
    """Dostawca, którego organizator nie założył, to ten sam brak zasobu, co brak flagi."""
    client, endpoint, _fee = paying
    body = body_of()
    stamp = int(timezone.now().timestamp())
    response = client.post(
        path("kogos-innego"),
        data=body,
        content_type="application/json",
        **{
            f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}": signature_header(
                endpoint.secret, body, stamp
            )
        },
    )
    assert response.status_code == 404


def test_get_is_not_allowed(paying):
    """Wyłącznie ``POST``: pod tym adresem nie ma czego czytać."""
    client, endpoint, _fee = paying
    assert client.get(path(endpoint.provider)).status_code == 405


# --- podpis: przez HTTP ------------------------------------------------------------------------------


def test_correct_signature_records_the_payment(paying):
    """Poprawny podpis: 200, ``matched=true``, należność zapłacona, wiersz w dzienniku."""
    client, endpoint, fee = paying
    paid_at = timezone.now() - timedelta(minutes=3)
    response = signed(client, endpoint, body_of(paid_at=paid_at.isoformat(), amount=str(AMOUNT)))
    assert response.status_code == 200
    assert response.json() == {"matched": True}
    fee.refresh_from_db()
    assert fee.status == FeeStatus.PAID
    assert fee.external_reference == REFERENCE
    assert abs((fee.paid_at - paid_at).total_seconds()) < 1
    event = PaymentEvent.objects.get()
    assert event.matched is True
    assert event.unmatched_reason == ""
    assert event.fee_id == fee.pk
    # Ładunku nie przechowujemy – w wierszu jest wyłącznie jego skrót.
    assert len(event.payload_hash) == 64


def test_bad_signature_gets_401_and_leaves_nothing(paying):
    """Zły sekret: 401, żadnego wiersza i żadnej zmiany w rejestrze."""
    client, endpoint, fee = paying
    body = body_of()
    stamp = int(timezone.now().timestamp())
    response = client.post(
        path(endpoint.provider),
        data=body,
        content_type="application/json",
        **{
            f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}": signature_header(
                "nie ten sekret", body, stamp
            )
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_SIGNATURE"
    fee.refresh_from_db()
    assert fee.status == FeeStatus.DUE
    assert not PaymentEvent.objects.exists()


def test_missing_signature_header_gets_401(paying):
    """Brak nagłówka to ta sama odmowa i ta sama treść, co zły podpis – bez wyroczni."""
    client, endpoint, _fee = paying
    response = client.post(path(endpoint.provider), data=body_of(), content_type="application/json")
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_SIGNATURE"


def test_signature_past_tolerance_gets_401(paying):
    """Nagranie sprzed godziny ma poprawny podpis – i właśnie po to w podpisie jest znacznik czasu."""
    client, endpoint, fee = paying
    stale = int(timezone.now().timestamp()) - TOLERANCE_SECONDS - 60
    response = signed(client, endpoint, body_of(), timestamp=stale)
    assert response.status_code == 401
    fee.refresh_from_db()
    assert fee.status == FeeStatus.DUE
    assert not PaymentEvent.objects.exists()


def test_rotated_secret_invalidates_the_old_one(paying):
    """Wymiana sekretu działa **natychmiast**: stary podpis przestaje być podpisem."""
    client, endpoint, _fee = paying
    old_secret = endpoint.secret
    rotate_payment_secret(endpoint)
    endpoint.refresh_from_db()
    assert endpoint.secret != old_secret
    assert endpoint.rotated_at is not None
    body = body_of()
    stamp = int(timezone.now().timestamp())
    response = client.post(
        path(endpoint.provider),
        data=body,
        content_type="application/json",
        **{f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}": signature_header(old_secret, body, stamp)},
    )
    assert response.status_code == 401
    assert signed(client, endpoint, body_of()).status_code == 200


def test_rotation_never_writes_a_secret_to_the_audit_log(paying):
    """Wpis audytowy czyta się w panelu i eksportuje do pliku – sekret nie ma tam czego szukać."""
    client, endpoint, _fee = paying
    old_secret = endpoint.secret
    rotate_payment_secret(endpoint)
    endpoint.refresh_from_db()
    entry = AuditLog.objects.filter(action="payment_endpoint.secret_rotated").get()
    serialized = json.dumps(entry.diff, ensure_ascii=False)
    assert old_secret not in serialized
    assert endpoint.secret not in serialized


# --- idempotencja ------------------------------------------------------------------------------------


def test_repeated_delivery_changes_nothing(paying):
    """Powtórka: jeden wiersz, jedna data wpłaty, jedna odpowiedź ``matched=true``."""
    client, endpoint, fee = paying
    first = signed(client, endpoint, body_of())
    assert first.status_code == 200
    fee.refresh_from_db()
    paid_at = fee.paid_at
    for _ in range(2):
        again = signed(client, endpoint, body_of())
        assert again.status_code == 200
        assert again.json() == {"matched": True}
    fee.refresh_from_db()
    assert fee.paid_at == paid_at
    assert PaymentEvent.objects.count() == 1
    assert AuditLog.objects.filter(action="fee.payment_recorded").count() == 1
    assert AuditLog.objects.filter(action="payment_webhook.received").count() == 1


def test_event_id_is_the_replay_key_when_the_provider_sends_one(paying):
    """Dostawca z własnym identyfikatorem zdarzenia: dwa zdarzenia o tej samej wpłacie są dwoma."""
    client, endpoint, _fee = paying
    assert signed(client, endpoint, body_of(event_id="evt-1")).status_code == 200
    assert signed(client, endpoint, body_of(event_id="evt-1")).status_code == 200
    assert PaymentEvent.objects.count() == 1
    # Drugie zdarzenie tej samej wpłaty (u dostawców bywa nim korekta albo powiadomienie
    # o rozliczeniu) jest osobnym wierszem w dzienniku – i **nie** zapisuje drugiej wpłaty,
    # bo ``record_payment`` jest idempotentny po identyfikatorze wpłaty.
    assert signed(client, endpoint, body_of(event_id="evt-2")).status_code == 200
    assert PaymentEvent.objects.count() == 2
    assert AuditLog.objects.filter(action="fee.payment_recorded").count() == 1


# --- czego nie da się dopasować ------------------------------------------------------------------------


def test_unknown_reference_is_recorded_and_answered_2xx(paying):
    """Nieznana należność: 2xx, wiersz z ``matched=False`` i powodem do ręcznego uzgodnienia."""
    client, endpoint, fee = paying
    body = json.dumps({"reference": "CZYJS-INNY-PRZELEW"}).encode("utf-8")
    response = signed(client, endpoint, body)
    assert response.status_code == 200
    assert response.json() == {"matched": False}
    event = PaymentEvent.objects.get()
    assert event.matched is False
    assert event.unmatched_reason == UNMATCHED_UNKNOWN_REFERENCE
    assert event.fee_id is None
    fee.refresh_from_db()
    assert fee.status == FeeStatus.DUE


def test_status_other_than_paid_is_not_a_payment(paying):
    """Zdarzenie, które nie jest potwierdzeniem wpłaty, nie staje się wpłatą przez sam fakt dojścia."""
    client, endpoint, fee = paying
    response = signed(client, endpoint, body_of(status="failed"))
    assert response.json() == {"matched": False}
    assert PaymentEvent.objects.get().unmatched_reason == UNMATCHED_IGNORED_STATUS
    fee.refresh_from_db()
    assert fee.status == FeeStatus.DUE


def test_waived_fee_is_a_case_for_a_human(paying):
    """Należność umorzona: wpłaty nie zapisujemy, ale doręczenie zostaje razem z powodem."""
    from apps.tenancy.fees import waive_fee

    client, endpoint, fee = paying
    waive_fee(fee, reason="Decyzja organizatora")
    response = signed(client, endpoint, body_of())
    assert response.status_code == 200
    assert response.json() == {"matched": False}
    event = PaymentEvent.objects.get()
    assert event.unmatched_reason == UNMATCHED_FEE_NOT_DUE
    assert event.fee_id == fee.pk


@pytest.mark.parametrize(
    "raw",
    [b"to nie jest JSON", b"[]", b'{"reference": ""}', b'{"reference": "A", "paid_at": "wczoraj"}'],
)
def test_unreadable_payload_gets_400(paying, raw):
    """Bajty, których nie da się przeczytać, to błąd adaptera – 400, i żadnego wiersza."""
    client, endpoint, _fee = paying
    assert signed(client, endpoint, raw).status_code == 400
    assert not PaymentEvent.objects.exists()


# --- izolacja konkursów -------------------------------------------------------------------------------


def test_secret_of_one_competition_does_not_open_another(paying, competition, settings):
    """Sekret konkursu B pod domeną konkursu A jest niczym – nawet gdy podpis jest poprawny."""
    from conftest import allow_test_hosts

    allow_test_hosts(settings)
    _client, endpoint, _fee = paying
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    host = competition.primary_domain
    other_client = APIClient(HTTP_HOST=host, SERVER_NAME=host)
    body = body_of()
    stamp = int(timezone.now().timestamp())
    response = other_client.post(
        path(endpoint.provider),
        data=body,
        content_type="application/json",
        **{
            f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}": signature_header(
                endpoint.secret, body, stamp
            )
        },
    )
    assert response.status_code == 404
    assert not PaymentEvent.objects.exists()


def test_panel_lists_are_scoped_to_the_competition(paying, competition, other_competition):
    """Ekran koordynatora (T42) widzi wyłącznie swoje poświadczenia i swoje doręczenia."""
    client, endpoint, _fee = paying
    signed(client, endpoint, body_of())
    assert [row.pk for row in payment_endpoints_for(other_competition)] == [endpoint.pk]
    assert payment_endpoints_for(competition) == []
    assert len(payment_events_for(other_competition)) == 1
    assert payment_events_for(competition) == []
    assert len(payment_events_for(other_competition, matched=False)) == 0


def test_two_competitions_may_use_the_same_provider_slug(paying, competition):
    """Ten sam dostawca u dwóch organizatorów to dwa wiersze i dwa **różne** sekrety."""
    _client, endpoint, _fee = paying
    competition.feature_flags = {**(competition.feature_flags or {}), "fees": True}
    competition.save(update_fields=["feature_flags"])
    mine = create_payment_endpoint(competition, provider=PROVIDER)
    assert mine.secret != endpoint.secret
    assert PaymentEndpoint.objects.filter(provider=PROVIDER).count() == 2


# --- audyt ---------------------------------------------------------------------------------------------


def test_audit_carries_no_amount_and_no_reference(paying):
    """W audycie webhooka nie ma kwoty ani tytułu przelewu: obie rzeczy prowadzą wprost do osoby.

    Co się stało z pieniędzmi, zapisuje ``fee.payment_recorded`` z ``record_payment`` – tam, gdzie
    dostęp ma koordynator konkursu, a nie tam, gdzie ślad zostawia ruch z zewnątrz.
    """
    client, endpoint, _fee = paying
    signed(client, endpoint, body_of(amount=str(AMOUNT)))
    entry = AuditLog.objects.filter(action="payment_webhook.received").get()
    assert entry.diff == {"provider": PROVIDER, "matched": True, "reason": ""}
    assert entry.actor_id is None
    assert AuditLog.objects.filter(action="fee.payment_recorded").exists()


def test_paid_status_is_the_default_shape(paying):
    """Ładunek bez pola ``status`` jest potwierdzeniem – najprostszy możliwy kształt działa."""
    client, endpoint, fee = paying
    assert signed(client, endpoint, body_of(status=PAID_STATUS)).status_code == 200
    fee.refresh_from_db()
    assert fee.status == FeeStatus.PAID
