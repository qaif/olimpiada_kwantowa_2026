"""Logowanie redaktora tokenem z ``/cms/`` aplikacji głównej (SSO, DJ-02 § 1.2 D6, S11).

Redaktorzy nie mają w djcms haseł ani kont zakładanych ręcznie (decyzja użytkownika z 26.09.2026).
Kto redaguje który konkurs, rozstrzyga aplikacja główna (``backend/apps/cms/djcms_sso.py`` – tam
format tokenu i mapowanie uprawnień) i przysyła wynik w jednorazowym tokenie, wysłanym formularzem
``POST`` na ``/djcms/sso/`` tego samego hosta. Tu:

1. :func:`verify_token` – podpis HMAC-SHA256 (``DJCMS_SSO_KEY``, porównanie w czasie stałym),
   wersja, odbiorca ``djcms``, host tokenu = host żądania, ``iat``/``exp`` (najwyżej
   :data:`MAX_TTL_SECONDS`, tolerancja zegara :data:`CLOCK_SKEW_SECONDS`), kształt każdego pola,
2. :func:`consume_nonce` – jednorazowość (``SsoNonce`` z ``unique``: drugi raz ten sam token nie
   loguje, także przy dwóch równoległych próbach),
3. :func:`provision_user` – konto ``web:<id>`` z ``is_staff``, **bez** hasła i **bez**
   ``is_superuser``; grupy ustawiane **dokładnie** na te z tokenu (``apps.sites.permissions``),
   więc utrata uprawnień w aplikacji głównej odbiera je tu przy najbliższym przejściu.

Nagłówek ``Origin`` (równy własnemu hostowi) i metodę ``POST`` sprawdza widok
(``apps.sites.views.sso_login``). Kolejność jest istotna: podpis przed czymkolwiek, co pisze do
bazy – śmieciowe żądanie nie zużywa ani jednego zapisu.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import SsoNonce

TOKEN_PREFIX = "v1"
TOKEN_VERSION = 1
AUDIENCE = "djcms"
ISSUER = "web"
#: Kontekst podpisu – ten sam co w ``backend/apps/cms/djcms_sso.py::SIGNING_CONTEXT``.
SIGNING_CONTEXT = b"olimpiada/djcms-sso/v1."
KEY_MIN_LENGTH = 32
#: Najdłuższa ważność, jaką przyjmujemy (``exp - iat``) – aplikacja główna wystawia dokładnie tyle.
MAX_TTL_SECONDS = 60
#: Tolerancja rozjazdu zegarów ``web`` i ``djcms`` (ten sam host, ale osobne kontenery).
CLOCK_SKEW_SECONDS = 5
#: Górna granica długości tokenu – odrzucamy przed dekodowaniem (lista konkursów to kilkadziesiąt wpisów).
MAX_TOKEN_LENGTH = 16_384
MAX_COMPETITIONS = 500

ABILITY_EDIT = "edit"
ABILITY_PUBLISH = "publish"
ABILITIES = frozenset({ABILITY_EDIT, ABILITY_PUBLISH})

#: Prefiks nazw kont z SSO. Zarezerwowany: konto o takiej nazwie należy do aplikacji głównej.
USERNAME_PREFIX = "web:"

SLUG_RE = re.compile(r"^[a-z0-9-]{1,50}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SsoError(Exception):
    """Token odrzucony. ``reason`` trafia do logu (bez tokenu), użytkownik widzi jedno zdanie."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SsoClaims:
    sub: int
    email: str
    first_name: str
    last_name: str
    host: str
    platform: bool
    #: ``{slug: frozenset(umiejętności)}`` – wyłącznie wpisy z ``edit``.
    competitions: dict[str, frozenset[str]]
    nonce: str
    issued_at: int
    expires_at: int

    @property
    def username(self) -> str:
        return f"{USERNAME_PREFIX}{self.sub}"

    def allows(self, slug: str) -> bool:
        return self.platform or ABILITY_EDIT in self.competitions.get(slug, frozenset())


def sso_key() -> str:
    return getattr(settings, "DJCMS_SSO_KEY", "") or ""


def sso_enabled() -> bool:
    return len(sso_key()) >= KEY_MIN_LENGTH


def _b64decode(value: str) -> bytes:
    if not B64_RE.match(value):
        raise SsoError("zły base64")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except binascii.Error, ValueError:
        raise SsoError("zły base64") from None


def signature(body: str, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), SIGNING_CONTEXT + body.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _str(payload: dict, name: str, *, limit: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise SsoError(f"pole {name}")
    return value


def _int(payload: dict, name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SsoError(f"pole {name}")
    return value


def _competitions(raw) -> dict[str, frozenset[str]]:
    if not isinstance(raw, list) or len(raw) > MAX_COMPETITIONS:
        raise SsoError("pole competitions")
    result: dict[str, frozenset[str]] = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"slug", "abilities"}:
            raise SsoError("wpis competitions")
        slug, abilities = item["slug"], item["abilities"]
        if not isinstance(slug, str) or not SLUG_RE.match(slug) or slug in result:
            raise SsoError("slug w competitions")
        if not isinstance(abilities, list) or not all(isinstance(a, str) for a in abilities):
            raise SsoError("abilities w competitions")
        granted = frozenset(abilities)
        if not granted <= ABILITIES:
            raise SsoError("nieznana umiejętność w competitions")
        if ABILITY_EDIT in granted:
            result[slug] = granted
    return result


def verify_token(token: str, *, host: str, now: float | None = None) -> SsoClaims:
    """Sprawdza token i zwraca jego treść albo rzuca :class:`SsoError`. **Nie** zużywa nonce'a."""
    key = sso_key()
    if len(key) < KEY_MIN_LENGTH:
        raise SsoError("SSO wyłączone (brak DJCMS_SSO_KEY)")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise SsoError("brak tokenu albo za długi")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise SsoError("zły kształt tokenu")
    _prefix, body, given = parts
    if not B64_RE.match(body) or not B64_RE.match(given):
        raise SsoError("zły kształt tokenu")
    if not hmac.compare_digest(signature(body, key).encode("ascii"), given.encode("ascii")):
        raise SsoError("zły podpis")
    try:
        payload = json.loads(_b64decode(body))
    except ValueError:
        raise SsoError("treść nie jest JSON-em") from None
    if not isinstance(payload, dict):
        raise SsoError("treść nie jest obiektem")
    if payload.get("v") != TOKEN_VERSION or payload.get("aud") != AUDIENCE or payload.get("iss") != ISSUER:
        raise SsoError("wersja, odbiorca albo wystawca")
    if _str(payload, "host", limit=253) != host or not host:
        raise SsoError("token dla innego hosta")
    issued_at, expires_at = _int(payload, "iat"), _int(payload, "exp")
    current = time.time() if now is None else now
    if issued_at > current + CLOCK_SKEW_SECONDS:
        raise SsoError("token z przyszłości")
    if expires_at <= current:
        raise SsoError("token przeterminowany")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TTL_SECONDS:
        raise SsoError("za długa ważność tokenu")
    nonce = _str(payload, "nonce", limit=64)
    if not NONCE_RE.match(nonce):
        raise SsoError("pole nonce")
    platform = payload.get("platform")
    if not isinstance(platform, bool):
        raise SsoError("pole platform")
    return SsoClaims(
        sub=_int(payload, "sub"),
        email=_str(payload, "email", limit=254),
        first_name=_str(payload, "first_name", limit=150),
        last_name=_str(payload, "last_name", limit=150),
        host=host,
        platform=platform,
        competitions=_competitions(payload.get("competitions")),
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def consume_nonce(claims: SsoClaims) -> None:
    """Zapisuje nonce jako zużyty; drugi zapis (powtórka tokenu) → :class:`SsoError`.

    Przy okazji sprząta nonce'y po terminie – dłużej niż ważność tokenu nie są do niczego potrzebne
    (przeterminowanego tokenu i tak nie przyjmie :func:`verify_token`).
    """
    expires = datetime.fromtimestamp(claims.expires_at, tz=UTC)
    SsoNonce.objects.filter(expires_at__lt=datetime.fromtimestamp(time.time(), tz=UTC)).delete()
    try:
        with transaction.atomic():
            SsoNonce.objects.create(nonce=claims.nonce, expires_at=expires)
    except IntegrityError:
        raise SsoError("token już użyty") from None


def provision_user(claims: SsoClaims):
    """Konto ``web:<id>`` z uprawnieniami **dokładnie** z tokenu. Zwraca konto (także zablokowane).

    Konto zablokowane w djcms (``is_active=False`` – decyzja operatora) zostaje zablokowane: SSO go
    nie odblokowuje, ale grupy i tak dostaje z tokenu, żeby odblokowanie nie przywróciło starych.
    """
    from django.contrib.auth.models import User

    from .permissions import apply_grants

    user, created = User.objects.get_or_create(
        username=claims.username,
        defaults={"email": claims.email, "is_staff": True, "is_active": True},
    )
    user.email = claims.email
    user.first_name = claims.first_name
    user.last_name = claims.last_name
    user.is_staff = True
    user.is_superuser = False
    if created or user.has_usable_password():
        user.set_unusable_password()
    user.save()
    apply_grants(user, platform=claims.platform, competitions=claims.competitions)
    return user


# --- sesja redaktora z SSO ---------------------------------------------------------------------

#: Klucz sesji z terminem ważności logowania SSO (sekundy epoki) – sprawdza go
#: ``apps.sites.middleware.EditorAccessMiddleware`` przy każdym żądaniu.
SESSION_KEY = "dj_sso_until"


def session_seconds() -> int:
    """Najdłuższy czas sesji z SSO (``DJCMS_SSO_SESSION_SECONDS``) – liczony od logowania, nie od ruchu.

    Po nim redaktor wraca do ``/cms/`` i przechodzi ponownie: aplikacja główna liczy wtedy
    uprawnienia od nowa, więc odebranie roli w aplikacji głównej zamyka dostęp najpóźniej po tym
    czasie, także w sesji otwartej wcześniej.
    """
    return int(getattr(settings, "DJCMS_SSO_SESSION_SECONDS", 2 * 60 * 60))


def start_session(request, user) -> None:
    """Loguje konto (nowy klucz sesji) i zapisuje termin ważności sesji SSO."""
    from django.contrib.auth import login

    login(request, user, backend="apps.pages.auth.ThrottledModelBackend")
    seconds = session_seconds()
    request.session[SESSION_KEY] = int(time.time()) + seconds
    request.session.set_expiry(seconds)


def session_expired(request) -> bool:
    """Czy sesja pochodzi z SSO i minął jej termin (sesja logowania hasłem nie ma znacznika)."""
    until = request.session.get(SESSION_KEY)
    return isinstance(until, int) and until <= time.time()
