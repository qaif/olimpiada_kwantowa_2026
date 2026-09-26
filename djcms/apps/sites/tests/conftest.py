"""Fikstury SSO i uprawnień redaktorów (DJ-02g): klucz, fabryka tokenów, logowanie przez ``/djcms/sso/``.

Token budujemy tutaj **niezależnie** od kodu aplikacji głównej (ten sam format, własne złożenie),
a zgodność obu implementacji co do bajtu sprawdza wspólny wektor ``sso_token_cases.json``
(``test_sso.py::test_contract_vector``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

import pytest

SSO_KEY = "k" * 40
SIGNING_CONTEXT = b"olimpiada/djcms-sso/v1."


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_payload(payload: dict, key: str = SSO_KEY) -> str:
    body = b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    digest = hmac.new(key.encode(), SIGNING_CONTEXT + body.encode(), hashlib.sha256).digest()
    return f"v1.{body}.{b64(digest)}"


def grant(slug: str, *abilities: str) -> dict:
    return {"slug": slug, "abilities": list(abilities or ("edit", "publish"))}


def make_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
        "v": 1,
        "aud": "djcms",
        "iss": "web",
        "sub": 7,
        "email": "koordynator@example.com",
        "first_name": "Kora",
        "last_name": "Dynator",
        "host": "testserver",
        "platform": False,
        "competitions": [grant("kwantowa")],
        "nonce": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + 60,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def sso_key(settings):
    settings.DJCMS_SSO_KEY = SSO_KEY
    return SSO_KEY


@pytest.fixture
def sso_post(client, sso_key):
    """``sso_post(token=None, host=…, prefix=…, origin=<własny>, **payload)`` – odpowiedź widoku."""

    def _post(
        token: str | None = None, *, host: str = "testserver", prefix: str = "", origin=None, **payload
    ):
        if token is None:
            token = sign_payload(make_payload(**{"host": host, **payload}))
        headers = {"HTTP_HOST": host}
        if origin is not False:
            headers["HTTP_ORIGIN"] = origin or f"http://{host}"
        return client.post(f"{prefix}/djcms/sso/", {"token": token}, **headers)

    return _post


@pytest.fixture
def sso_editor(client, sso_post):
    """``sso_editor(*grants, platform=False, host=…, prefix=…)`` – klient zalogowany przez SSO."""

    def _login(*grants, platform: bool = False, host: str = "testserver", prefix: str = "", sub: int = 7):
        response = sso_post(
            host=host,
            prefix=prefix,
            platform=platform,
            competitions=list(grants) or [grant("kwantowa")],
            sub=sub,
        )
        assert response.status_code == 302, response.content[:300]
        return client

    return _login
