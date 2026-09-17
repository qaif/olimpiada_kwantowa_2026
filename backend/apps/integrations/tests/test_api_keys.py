"""Klucze API: wystawianie, skrót zamiast klucza, zakresy, unieważnienie i limit żądań."""

import pytest
from django.urls import reverse

from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.integrations.models import (
    SCOPE_READ_PARTICIPANTS,
    SCOPE_READ_PARTICIPANTS_PII,
    SCOPE_READ_RESULTS,
    ApiKey,
    hash_secret,
)
from apps.integrations.services import create_api_key, revoke_api_key

pytestmark = pytest.mark.django_db

CAPABILITIES = "/api/v1/"


def test_issued_key_is_stored_only_as_hash(make_key):
    """W bazie leży skrót sekretu i przedrostek – nigdy sam klucz."""
    key, token = make_key(name="Kuratorium Mazowieckie", scopes=[SCOPE_READ_RESULTS], pii_allowed=False)

    marker, prefix, secret = token.split("_", 2)
    assert marker == "ok"
    assert prefix == key.prefix
    assert key.key_hash == hash_secret(secret)
    # Sekret nie może dać się odtworzyć z żadnego pola wiersza.
    stored = " ".join(str(value) for value in ApiKey.objects.filter(pk=key.pk).values()[0].values())
    assert secret not in stored


def test_scopes_are_validated_and_canonical(make_key):
    """Kolejność zakresów jest ustalona, a nieznany zakres nie przechodzi."""
    key, _ = make_key(scopes=[SCOPE_READ_RESULTS, SCOPE_READ_PARTICIPANTS])

    assert key.scopes == [SCOPE_READ_PARTICIPANTS, SCOPE_READ_RESULTS]
    with pytest.raises(DomainError) as exc:
        create_api_key(name="Zły", scopes=["read:everything"])
    assert exc.value.machine_code == "INVALID_SCOPES"


def test_key_without_scopes_is_refused():
    with pytest.raises(DomainError) as exc:
        create_api_key(name="Pusty", scopes=[])
    assert exc.value.machine_code == "SCOPES_REQUIRED"


def test_pii_scope_requires_explicit_flag():
    """Zakres do danych osobowych nie działa bez drugiej, osobnej zgody koordynatora."""
    with pytest.raises(DomainError) as exc:
        create_api_key(name="PII", scopes=[SCOPE_READ_PARTICIPANTS_PII], pii_allowed=False)
    assert exc.value.machine_code == "INVALID_API_KEY"


def test_has_scope_follows_pii_flag(make_key):
    """Cofnięcie flagi odbiera dostęp do danych osobowych bez odbierania zakresu."""
    key, _ = make_key(scopes=[SCOPE_READ_PARTICIPANTS, SCOPE_READ_PARTICIPANTS_PII], pii_allowed=True)
    assert key.has_scope(SCOPE_READ_PARTICIPANTS_PII)

    key.pii_allowed = False
    assert not key.has_scope(SCOPE_READ_PARTICIPANTS_PII)
    assert key.has_scope(SCOPE_READ_PARTICIPANTS)


def test_rate_limit_must_be_sane():
    with pytest.raises(DomainError) as exc:
        create_api_key(name="Bez limitu", scopes=[SCOPE_READ_RESULTS], rate_limit_per_minute=0)
    assert exc.value.machine_code == "INVALID_RATE_LIMIT"


def test_creation_and_revocation_leave_audit_trail(make_key, coordinator):
    key, _ = make_key(name="Uczelnia")
    revoke_api_key(key, actor=coordinator)

    actions = set(
        AuditLog.objects.filter(target_type="integrations.apikey", target_id=str(key.pk)).values_list(
            "action", flat=True
        )
    )
    assert actions == {"apikey.created", "apikey.revoked"}
    created = AuditLog.objects.get(action="apikey.created", target_id=str(key.pk))
    assert created.diff["prefix"] == key.prefix
    assert "secret" not in created.diff


def test_revoking_twice_is_a_conflict(make_key, coordinator):
    key, _ = make_key()
    revoke_api_key(key, actor=coordinator)
    with pytest.raises(DomainError) as exc:
        revoke_api_key(key, actor=coordinator)
    assert exc.value.machine_code == "API_KEY_ALREADY_REVOKED"


# --- uwierzytelnienie żądania -------------------------------------------------------------------


def test_valid_key_opens_capabilities(authed):
    client, key = authed(name="Partner")
    response = client.get(CAPABILITIES)

    assert response.status_code == 200
    assert response.data["key_prefix"] == key.prefix
    assert response.data["scopes"] == key.scopes


def test_missing_header_is_unauthorized(api_client):
    response = api_client.get(CAPABILITIES)
    assert response.status_code == 401


@pytest.mark.parametrize("token", ["nonsense", "ok_abc", "ok__secret", "Bearer"])
def test_malformed_token_is_unauthorized(api_client, token):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = api_client.get(CAPABILITIES)

    assert response.status_code == 401
    assert response.data["code"] == "INVALID_API_KEY"


def test_wrong_secret_is_unauthorized(api_client, make_key):
    key, _ = make_key()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer ok_{key.prefix}_nie-ten-sekret")
    response = api_client.get(CAPABILITIES)

    assert response.status_code == 401
    assert response.data["code"] == "INVALID_API_KEY"


def test_revoked_key_says_so(api_client, make_key, coordinator):
    """Unieważnienie ma **własny** kod – partner ma szukać nowego klucza, a nie literówki."""
    key, token = make_key()
    revoke_api_key(key, actor=coordinator)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = api_client.get(CAPABILITIES)

    assert response.status_code == 401
    assert response.data["code"] == "API_KEY_REVOKED"


def test_last_used_is_recorded(authed):
    client, key = authed()
    client.get(CAPABILITIES)

    key.refresh_from_db()
    assert key.last_used_at is not None


def test_rate_limit_is_per_key(authed):
    """Limit jest atrybutem klucza, a nie ustawieniem serwisu."""
    client, _ = authed(rate_limit_per_minute=2)

    assert client.get(CAPABILITIES).status_code == 200
    assert client.get(CAPABILITIES).status_code == 200
    blocked = client.get(CAPABILITIES)
    assert blocked.status_code == 429
    assert blocked.has_header("Retry-After")


def test_key_without_scope_is_forbidden_not_unauthorized(authed, stage):
    """Prawdziwy klucz bez zakresu dostaje 403 – wie, że poświadczenie działa."""
    client, _ = authed(scopes=[SCOPE_READ_RESULTS], pii_allowed=False)
    response = client.get(reverse("integrations:stage-participants", args=[stage.pk]))

    assert response.status_code == 403
    assert response.data["code"] == "MISSING_SCOPE"
