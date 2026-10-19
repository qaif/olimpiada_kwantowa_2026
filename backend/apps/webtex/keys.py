"""Klucze i token SSO webTEX (TEX-01 § „Kontrakt Django ↔ webTEX”).

Jeden sekret ``WEBTEX_SHARED_KEY`` i dwa klucze pochodne z separacją domen – obie strony liczą je
identycznie::

    sso_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-sso-v1")
    api_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-api-v1")

Token SSO z kluczem ``sso_key`` nie da się więc użyć jako podpisu żądania API i odwrotnie.
JWT HS256 wystawiamy biblioteką standardową, tak samo jak przepustki Jitsi
(``apps.competitions.jitsi_jwt.encode_hs256`` – stamtąd kodowanie base64url i kształt nagłówka);
weryfikacji tokenów platforma nie robi wcale, robi ją webTEX.

Token nie jest nigdzie zapisywany: powstaje w widoku, trafia do nagłówka ``Location`` odpowiedzi 302
(``Cache-Control: no-store``) we **fragmencie** adresu – fragment nie jedzie do serwera, więc nie ląduje
w logu proxy ani w ``Referer``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from django.conf import settings

#: Najkrótszy sekret, przy którym integracja w ogóle działa – ten sam próg, co przy pozostałych
#: sekretach współdzielonych (``cms.W013``, ``competitions.W001``).
KEY_MIN_LENGTH = 32
#: Ważność tokenu SSO (TEX-01: ``exp = iat + 120``; webTEX dopuszcza 30 s rozjazdu zegara).
TOKEN_TTL_SECONDS = 120
#: Odbiorca tokenu – stała kontraktu.
AUDIENCE = "webtex"

SSO_CONTEXT = b"webtex-sso-v1"
API_CONTEXT = b"webtex-api-v1"


def shared_key() -> str:
    return getattr(settings, "WEBTEX_SHARED_KEY", "") or ""


def public_url() -> str:
    """Publiczny adres edytora (``https://tex.<domena>``) bez ukośnika na końcu."""
    return (getattr(settings, "WEBTEX_URL", "") or "").rstrip("/")


def internal_url() -> str:
    """Adres webTEX w sieci wewnętrznej compose (``http://webtex:3000``) bez ukośnika na końcu."""
    return (getattr(settings, "WEBTEX_INTERNAL_URL", "") or "").rstrip("/")


def issuer() -> str:
    return getattr(settings, "WEBTEX_ISSUER", "") or "olimpiada"


def configured() -> bool:
    """Klucz dość długi i znany adres edytora. Krótszy klucz działa jak pusty – token nie powstaje."""
    return len(shared_key()) >= KEY_MIN_LENGTH and bool(public_url()) and bool(internal_url())


def _derive(context: bytes) -> bytes:
    return hmac.new(shared_key().encode("utf-8"), context, hashlib.sha256).digest()


def sso_key() -> bytes:
    return _derive(SSO_CONTEXT)


def api_key() -> bytes:
    return _derive(API_CONTEXT)


def key_problems() -> list[str]:
    """Powody ostrzeżenia ``webtex.W001`` (pusty klucz to nie problem – integracja jest wtedy wyłączona)."""
    key = shared_key()
    if not key:
        return []
    problems = []
    if len(key) < KEY_MIN_LENGTH:
        problems.append(
            f"WEBTEX_SHARED_KEY ma {len(key)} znaków – edytor LaTeX wymaga co najmniej "
            f"{KEY_MIN_LENGTH} i do tego czasu jest wyłączony."
        )
    others = {
        "SECRET_KEY": getattr(settings, "SECRET_KEY", "") or "",
        "DJCMS_SSO_KEY": getattr(settings, "DJCMS_SSO_KEY", "") or "",
        "DJCMS_INTERNAL_TOKEN": getattr(settings, "DJCMS_INTERNAL_TOKEN", "") or "",
        "JITSI_JWT_APP_SECRET": getattr(settings, "JITSI_JWT_APP_SECRET", "") or "",
        "LIVEKIT_API_SECRET": getattr(settings, "LIVEKIT_API_SECRET", "") or "",
    }
    for name, value in others.items():
        if value and hmac.compare_digest(value.encode(), key.encode()):
            problems.append(f"WEBTEX_SHARED_KEY jest równy {name} – każdy sekret ma być osobny.")
    return problems


# --- token SSO ----------------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_hs256(claims: dict, key: bytes) -> str:
    """``base64url(nagłówek).base64url(treść).base64url(HMAC-SHA256)`` – jak ``jitsi_jwt.encode_hs256``,
    ale z kluczem binarnym (``sso_key`` to 32 bajty skrótu, nie napis)."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"


def claims_for(
    *,
    sub: str,
    name: str,
    lang: str,
    project: str,
    scope: str,
    share: bool,
    ai: bool = False,
    title: str,
    code: str,
    return_url: str,
    now: int | None = None,
) -> dict:
    """Komplet claimów jednego tokenu. Osobno od podpisu, żeby testy czytały kształt wprost."""
    iat = int(time.time()) if now is None else int(now)
    return {
        "iss": issuer(),
        "aud": AUDIENCE,
        "iat": iat,
        "exp": iat + TOKEN_TTL_SECONDS,
        # 18 bajtów losowych → 24 znaki base64url (kontrakt: ≥ 16 bajtów).
        "jti": secrets.token_urlsafe(18),
        "sub": sub,
        "name": name,
        "lang": lang,
        "project": project,
        "scope": scope,
        "share": bool(share),
        # Uzupełnienie A, D9: brak claimu albo cokolwiek innego niż JSON ``true`` = asystent wyłączony.
        "ai": bool(ai),
        "title": title,
        "code": code,
        "return_url": return_url,
    }


def issue(claims: dict) -> str:
    if not configured():
        raise RuntimeError("Edytor LaTeX jest wyłączony (WEBTEX_SHARED_KEY pusty albo za krótki).")
    return encode_hs256(claims, sso_key())


def sso_url(token: str) -> str:
    """Adres otwarcia edytora: token we **fragmencie** (TEX-01 D3)."""
    return f"{public_url()}/sso#token={token}"


# --- podpis żądań API ---------------------------------------------------------------------------


def api_signature(method: str, path: str, body: bytes, *, timestamp: int | None = None) -> str:
    """Wartość nagłówka ``X-Webtex-Signature``: ``t=<unix>,v1=<hex HMAC>``.

    Podpisywany napis: ``"{t}.{METHOD}.{path}.{surowe ciało}"``; ``path`` bez query stringu.
    """
    t = int(time.time()) if timestamp is None else int(timestamp)
    message = f"{t}.{method.upper()}.{path}.".encode() + (body or b"")
    digest = hmac.new(api_key(), message, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"
