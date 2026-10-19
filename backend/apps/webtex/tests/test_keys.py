"""Kontrakt kryptograficzny z webTEX: klucze pochodne, token SSO i podpis API liczone w teście niezależnie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

import pytest

from apps.webtex import checks, keys

from .conftest import SHARED_KEY


def b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def independent_key(context: str) -> bytes:
    return hmac.new(SHARED_KEY.encode(), context.encode(), hashlib.sha256).digest()


def decode_token(token: str) -> tuple[dict, dict]:
    """Weryfikacja HS256 bez kodu aplikacji: ``sso_key`` wyprowadzony tutaj, podpis policzony tutaj."""
    header_b64, body_b64, signature_b64 = token.split(".")
    expected = hmac.new(
        independent_key("webtex-sso-v1"), f"{header_b64}.{body_b64}".encode(), hashlib.sha256
    ).digest()
    assert hmac.compare_digest(b64decode(signature_b64), expected)
    return json.loads(b64decode(header_b64)), json.loads(b64decode(body_b64))


def test_derived_keys_match_the_contract():
    assert keys.sso_key() == independent_key("webtex-sso-v1")
    assert keys.api_key() == independent_key("webtex-api-v1")
    assert keys.sso_key() != keys.api_key()
    assert len(keys.sso_key()) == 32


def _claims(**overrides):
    values = {
        "sub": "42",
        "name": "Ada Lovelace",
        "lang": "pl",
        "project": "kwantowa/s7/p3",
        "scope": "kwantowa/s7",
        "share": False,
        "title": "Zadanie 3. Splątanie",
        "code": "OK-ABC123",
        "return_url": "https://olimpiada.example.test/me/?tab=zadania#problem-9",
        "now": 1_700_000_000,
    }
    values.update(overrides)
    return keys.claims_for(**values)


def test_token_is_hs256_signed_with_sso_key_and_carries_contract_claims():
    header, claims = decode_token(keys.issue(_claims()))

    assert header == {"alg": "HS256", "typ": "JWT"}
    assert claims["iss"] == "olimpiada"
    assert claims["aud"] == "webtex"
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == claims["iat"] + 120
    assert len(b64decode(claims["jti"])) >= 16
    assert {k: claims[k] for k in ("sub", "name", "lang", "project", "scope", "share", "title", "code")} == {
        "sub": "42",
        "name": "Ada Lovelace",
        "lang": "pl",
        "project": "kwantowa/s7/p3",
        "scope": "kwantowa/s7",
        "share": False,
        "title": "Zadanie 3. Splątanie",
        "code": "OK-ABC123",
    }
    assert claims["return_url"].startswith("https://")
    # Uzupełnienie A, D9: ``ai`` zawsze obecne jako JSON bool – domyślnie ``false``.
    assert claims["ai"] is False
    # Kontrakt webTEX: ``exp - iat`` ≤ 300 s, ``jti`` ≥ 16 znaków z ``[A-Za-z0-9_-]``.
    assert claims["exp"] - claims["iat"] <= 300
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,}", claims["jti"])


def test_ai_claim_is_json_true_only_when_enabled():
    _header, claims = decode_token(keys.issue(_claims(ai=True)))
    assert claims["ai"] is True


def test_every_token_has_a_fresh_jti():
    assert _claims()["jti"] != _claims()["jti"]


def test_token_signed_with_api_key_would_not_verify():
    token = keys.encode_hs256(_claims(), keys.api_key())
    with pytest.raises(AssertionError):
        decode_token(token)


def test_issuer_comes_from_settings(settings):
    settings.WEBTEX_ISSUER = "iqo"
    assert _claims()["iss"] == "iqo"


def test_sso_url_puts_token_in_fragment():
    assert keys.sso_url("abc.def.ghi") == "https://tex.example.test/sso#token=abc.def.ghi"


def test_api_signature_vector():
    body = b'{"sub":"42","project":"kwantowa/s7/p3"}'
    header = keys.api_signature("post", "/api/integration/build", body, timestamp=1_700_000_000)

    expected = hmac.new(
        independent_key("webtex-api-v1"),
        b'1700000000.POST./api/integration/build.{"sub":"42","project":"kwantowa/s7/p3"}',
        hashlib.sha256,
    ).hexdigest()
    assert header == f"t=1700000000,v1={expected}"


def test_api_signature_of_empty_body():
    header = keys.api_signature("GET", "/api/integration/health", b"", timestamp=5)
    expected = hmac.new(independent_key("webtex-api-v1"), b"5.GET./api/integration/health.", hashlib.sha256)
    assert header == f"t=5,v1={expected.hexdigest()}"


def test_short_or_empty_key_disables_the_integration(settings):
    assert keys.configured()
    settings.WEBTEX_SHARED_KEY = "x" * 31
    assert not keys.configured()
    with pytest.raises(RuntimeError):
        keys.issue(_claims())
    settings.WEBTEX_SHARED_KEY = ""
    assert not keys.configured()
    settings.WEBTEX_SHARED_KEY = SHARED_KEY
    settings.WEBTEX_URL = ""
    assert not keys.configured()


def test_check_warns_about_short_and_reused_key(settings):
    settings.WEBTEX_SHARED_KEY = ""
    assert checks.webtex_shared_key(None) == []
    settings.WEBTEX_SHARED_KEY = "short"
    warnings = checks.webtex_shared_key(None)
    assert [w.id for w in warnings] == ["webtex.W001"]
    assert "5 znaków" in warnings[0].msg
    settings.WEBTEX_SHARED_KEY = settings.SECRET_KEY = "s" * 40
    assert any("SECRET_KEY" in w.msg for w in checks.webtex_shared_key(None))
    settings.SECRET_KEY = "other-secret-" + "y" * 40
    assert checks.webtex_shared_key(None) == []
