"""Klient LiveKit: tokeny dostępu, API serwera (Twirp), weryfikacja webhooków. Bez stanu i modeli.

LiveKit (Apache 2.0, serwer własny – ``deploy/livekit``) przyjmuje uczestnika pokoju wyłącznie
z **tokenem dostępu** (JWT HS256 podpisany sekretem API), a polecenia serwerowe (zmiana uprawnień
uczestnika, nagrywanie) – z tokenem z odpowiednim uprawnieniem w nagłówku ``Authorization``.
Platforma zna klucz i sekret API (``LIVEKIT_API_KEY`` / ``LIVEKIT_API_SECRET``); sekret nigdy nie
wychodzi z serwera (szablony i JS dostają wyłącznie gotowy, krótko żyjący token).

**Dlaczego bez pakietu ``livekit-api``.** Oficjalny pakiet ciągnie ``aiohttp`` i ``protobuf`` po to,
żeby wystawić JWT o stałym kształcie i wysłać kilka żądań JSON. Tokeny podpisujemy tym samym
dwudziestolinijkowym ``encode_hs256``, co przepustki Jitsi (``apps.competitions.jitsi_jwt``),
a API serwera LiveKit to Twirp: ``POST <host>/twirp/livekit.<Usługa>/<Metoda>`` z treścią JSON
(nazwy pól jak w ``.proto`` – ``protojson`` przyjmuje je wprost). Format claimów i nazwy pól są
z dokumentacji LiveKit („Authentication”, „Server APIs”, „Webhooks”); testy sprawdzają je
niezależną implementacją (PyJWT).

Kształt tokenu uczestnika::

    {"iss": <API key>, "sub": <identity>, "name": <nazwa>, "nbf", "exp",
     "video": {"room": <pokój>, "roomJoin": true, "canPublish": …, "canSubscribe": true,
               "canPublishData": true, "roomAdmin": …}}

Token żyje krótko (``LIVEKIT_TOKEN_TTL_SECONDS``, 10 min): służy do **wejścia**; połączenie raz
nawiązane LiveKit utrzymuje sam (odświeża je własnym tokenem sesji), więc krótki token nie
wyrzuca nikogo z trwającego webinaru, a skradziony – szybko przestaje otwierać pokój.

**Webhook** (``verify_webhook``): LiveKit podpisuje każde zdarzenie JWT-em w nagłówku
``Authorization`` – ``iss`` = klucz API, claim ``sha256`` = base64(SHA-256 treści). Bez poprawnego
podpisu, z innym kluczem, po terminie ważności albo z inną treścią – odmowa. Powtórki odcina
warstwa wyżej (``services.handle_webhook``: identyfikator zdarzenia raz, wiek zdarzenia).

Sieć idzie przez :func:`_http_post` – jedną funkcję, którą testy podmieniają na fałszywy serwer
(``apps/webinars/tests/fake_livekit.py``). Żaden test nie wychodzi do sieci.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlsplit

from django.conf import settings

from apps.competitions.jitsi_jwt import encode_hs256

logger = logging.getLogger(__name__)

#: Rozjazd zegarów platformy i serwera LiveKit tolerowany przy ``nbf`` (cofamy go) i przy
#: weryfikacji webhooków (``exp``/``nbf`` z zapasem).
CLOCK_SKEW_SECONDS = 30
#: Token poleceń serwerowych: jedno żądanie, więc minuta wystarcza z zapasem.
SERVER_TOKEN_TTL_SECONDS = 60
#: Najdłuższa treść webhooka, jaką przyjmujemy (bajty). Zdarzenie LiveKit to kilka kilobajtów.
MAX_WEBHOOK_BYTES = 256 * 1024


class LiveKitUnavailable(Exception):
    """Serwer LiveKit nie odpowiedział sensownie (sieć, limit czasu, kod HTTP, nie-JSON)."""


class LiveKitError(Exception):
    """Serwer odpowiedział błędem Twirp (``{"code": …, "msg": …}``) – np. ``not_found``."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code


class WebhookInvalid(Exception):
    """Webhook bez poprawnego podpisu, z innym kluczem, przeterminowany albo z podmienioną treścią."""


# --- konfiguracja --------------------------------------------------------------------------------


def ws_url() -> str:
    """Adres sygnalizacji dla przeglądarki (``wss://…``) albo pusty, gdy brak/niepoprawny.

    ``ws://`` wyłącznie przy ``DEBUG`` (serwer deweloperski ``livekit-server --dev``): na produkcji
    kamera i mikrofon działają tylko z bezpiecznego kontekstu, a token jedzie w tym połączeniu.
    """
    raw = (getattr(settings, "LIVEKIT_URL", "") or "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    allowed = ("wss",) if not settings.DEBUG else ("wss", "ws")
    if parts.scheme not in allowed or not parts.netloc:
        return ""
    return raw


def api_url() -> str:
    """Adres API serwera (Twirp): ``LIVEKIT_API_URL`` albo ``wss`` → ``https`` z ``LIVEKIT_URL``.

    Osobne ustawienie istnieje dla serwera w tej samej sieci compose (``http://livekit:7880``):
    polecenia serwerowe nie muszą wtedy wychodzić przez internet i Caddy.
    """
    explicit = (getattr(settings, "LIVEKIT_API_URL", "") or "").strip().rstrip("/")
    if explicit:
        return explicit if urlsplit(explicit).scheme in ("https", "http") else ""
    base = ws_url()
    if not base:
        return ""
    return "https" + base[3:] if base.startswith("wss") else "http" + base[2:]


def configured() -> bool:
    """Adres, klucz i sekret API – dopiero wtedy platforma wystawia tokeny."""
    return bool(ws_url()) and bool(api_key()) and bool(api_secret())


def api_key() -> str:
    return (getattr(settings, "LIVEKIT_API_KEY", "") or "").strip()


def api_secret() -> str:
    return (getattr(settings, "LIVEKIT_API_SECRET", "") or "").strip()


def csp_origins() -> tuple[str, ...]:
    """Originy serwera LiveKit do ``connect-src``: sygnalizacja (``wss://host``) i jej odpowiednik
    ``https://host`` (SDK sprawdza osiągalność serwera zwykłym ``fetch`` przy błędzie połączenia).
    Pusto bez konfiguracji – polityka instalacji bez webinarów nie zmienia się ani o znak."""
    if not configured():
        return ()
    parts = urlsplit(ws_url())
    secure = parts.scheme == "wss"
    return (f"{parts.scheme}://{parts.netloc}", f"{'https' if secure else 'http'}://{parts.netloc}")


# --- tokeny ---------------------------------------------------------------------------------------


def participant_grants(room: str, *, presenter: bool) -> dict:
    """Uprawnienia ``video`` dla roli. Widz: odbiera i pisze na czacie (``canPublishData`` – czat,
    podniesiona ręka), ale nie nadaje obrazu ani dźwięku. Prowadzący: nadaje i administruje pokojem."""
    grants = {
        "room": room,
        "roomJoin": True,
        "canSubscribe": True,
        "canPublishData": True,
        "canPublish": bool(presenter),
    }
    if presenter:
        grants["roomAdmin"] = True
    return grants


def access_token(
    *, identity: str, name: str, room: str, presenter: bool, ttl: int | None = None, now=None
) -> str:
    """Token wejścia do pokoju. ``identity`` – stały pseudonim osoby (bez e-maila i ``pk``)."""
    now = int(now if now is not None else time.time())
    ttl = int(ttl if ttl is not None else settings.LIVEKIT_TOKEN_TTL_SECONDS)
    claims = {
        "iss": api_key(),
        "sub": identity,
        "name": name,
        "nbf": now - CLOCK_SKEW_SECONDS,
        "exp": now + max(60, ttl),
        "video": participant_grants(room, presenter=presenter),
    }
    return encode_hs256(claims, api_secret())


def server_token(video: dict, now=None) -> str:
    """Token polecenia serwerowego z jednym uprawnieniem (``roomAdmin`` dla pokoju, ``roomRecord``)."""
    now = int(now if now is not None else time.time())
    claims = {
        "iss": api_key(),
        "nbf": now - CLOCK_SKEW_SECONDS,
        "exp": now + SERVER_TOKEN_TTL_SECONDS,
        "video": video,
    }
    return encode_hs256(claims, api_secret())


def _b64decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_hs256(token: str, secret: str, now=None) -> dict:
    """Weryfikacja JWT **tylko** HS256 – nagłówek ``alg`` innego rodzaju (w tym ``none``) to odmowa."""
    try:
        header_b64, body_b64, signature_b64 = token.split(".")
        header = json.loads(_b64decode(header_b64))
        claims = json.loads(_b64decode(body_b64))
        signature = _b64decode(signature_b64)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise WebhookInvalid("Nieczytelny token.") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256" or not isinstance(claims, dict):
        raise WebhookInvalid("Nieobsługiwany algorytm tokenu.")
    expected = hmac.new(secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookInvalid("Zły podpis.")
    now = int(now if now is not None else time.time())
    # ``exp`` i ``nbf`` są **obowiązkowe**: podpisany token bez terminu ważności byłby ważny zawsze,
    # czyli przechwycone zdarzenie dałoby się odtworzyć po latach (LiveKit wystawia oba claimy).
    try:
        expires, not_before = int(claims["exp"]), int(claims["nbf"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WebhookInvalid("Token bez terminu ważności.") from exc
    if expires + CLOCK_SKEW_SECONDS < now:
        raise WebhookInvalid("Token po terminie.")
    if not_before - CLOCK_SKEW_SECONDS > now:
        raise WebhookInvalid("Token jeszcze nieważny.")
    return claims


def verify_webhook(body: bytes, authorization: str, now=None) -> dict:
    """Zdarzenie webhooka po sprawdzeniu podpisu, klucza i skrótu treści. :class:`WebhookInvalid` inaczej."""
    if not configured():
        raise WebhookInvalid("LiveKit nie jest skonfigurowany.")
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookInvalid("Za duża treść.")
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise WebhookInvalid("Brak podpisu.")
    claims = decode_hs256(token, api_secret(), now=now)
    if not hmac.compare_digest(str(claims.get("iss", "")), api_key()):
        raise WebhookInvalid("Inny klucz API.")
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    if not hmac.compare_digest(str(claims.get("sha256", "")), digest):
        raise WebhookInvalid("Treść nie zgadza się z podpisem.")
    try:
        event = json.loads(body)
    except ValueError as exc:
        raise WebhookInvalid("Treść nie jest JSON-em.") from exc
    if not isinstance(event, dict):
        raise WebhookInvalid("Treść nie jest obiektem.")
    # Identyfikator i czas zdarzenia są warunkiem ochrony przed powtórką (``services.handle_webhook``):
    # zdarzenie bez nich nie dałoby się ani zapamiętać, ani odrzucić jako stare.
    if not str(event.get("id") or "").strip() or not str(event.get("createdAt") or "").strip():
        raise WebhookInvalid("Zdarzenie bez identyfikatora albo czasu.")
    return event


# --- API serwera (Twirp) --------------------------------------------------------------------------


def _http_post(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """Jedyne wyjście do sieci. Bez przekierowań – API serwera nie ma powodu nigdzie odsyłać."""
    import requests

    response = requests.post(url, data=body, headers=headers, timeout=timeout, allow_redirects=False)
    return response.status_code, response.content[:MAX_WEBHOOK_BYTES]


def twirp(service: str, method: str, payload: dict, video: dict) -> dict:
    """``POST /twirp/livekit.<service>/<method>``. Sekret i token nie trafiają do logu."""
    if not configured() or not api_url():
        raise LiveKitUnavailable("LiveKit nie jest skonfigurowany.")
    url = f"{api_url()}/twirp/livekit.{service}/{method}"
    headers = {"Authorization": f"Bearer {server_token(video)}", "Content-Type": "application/json"}
    timeout = float(getattr(settings, "LIVEKIT_TIMEOUT_SECONDS", 8) or 8)
    try:
        status, raw = _http_post(url, json.dumps(payload).encode(), headers, timeout)
    except Exception as exc:  # noqa: BLE001 - każda awaria sieci to ta sama odpowiedź dla człowieka
        logger.warning("LiveKit %s/%s: brak odpowiedzi (%s).", service, method, type(exc).__name__)
        raise LiveKitUnavailable("Serwer LiveKit nie odpowiada.") from exc
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise LiveKitUnavailable(f"Nieczytelna odpowiedź LiveKit (HTTP {status}).") from exc
    if status != 200:
        code = str(data.get("code", status)) if isinstance(data, dict) else str(status)
        logger.info("LiveKit %s/%s: %s.", service, method, code)
        if status >= 500 and code not in ("not_found",):
            raise LiveKitUnavailable(f"Serwer LiveKit odpowiedział kodem {status}.")
        raise LiveKitError(code, str(data.get("msg", "")) if isinstance(data, dict) else "")
    return data if isinstance(data, dict) else {}


def set_can_publish(room: str, identity: str, can_publish: bool) -> dict:
    """„Daj głos” / „odbierz głos”: ``UpdateParticipant`` z nowymi uprawnieniami (bez zmiany roli admina)."""
    return twirp(
        "RoomService",
        "UpdateParticipant",
        {
            "room": room,
            "identity": identity,
            "permission": {
                "can_subscribe": True,
                "can_publish": bool(can_publish),
                "can_publish_data": True,
            },
        },
        {"roomAdmin": True, "room": room},
    )


def remove_participant(room: str, identity: str) -> dict:
    return twirp(
        "RoomService",
        "RemoveParticipant",
        {"room": room, "identity": identity},
        {"roomAdmin": True, "room": room},
    )


def create_room(room: str, *, empty_timeout: int = 600, max_participants: int = 0) -> dict:
    """Zakłada pokój (idempotentnie – istniejący zwraca bez zmian). Serwer ma ``auto_create: false``
    (``deploy/livekit/livekit.yaml.example``), więc pokój istnieje **tylko** wtedy, gdy założyła go
    platforma po sprawdzeniu reguł – token sprzed „Zakończ” nie otworzy pokoju na nowo."""
    payload = {"name": room, "empty_timeout": int(empty_timeout)}
    if max_participants:
        payload["max_participants"] = int(max_participants)
    return twirp("RoomService", "CreateRoom", payload, {"roomCreate": True})


def list_egress(egress_id: str) -> list[dict]:
    """Stan jednego egressu (``Egress/ListEgress``) – do uzgodnienia nagrania, gdy webhook zaginął."""
    data = twirp("Egress", "ListEgress", {"egress_id": egress_id}, {"roomRecord": True})
    items = data.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def delete_room(room: str) -> dict:
    """Zamyka pokój: rozłącza wszystkich. Wymaga ``roomCreate`` (tak mówi dokumentacja ``DeleteRoom``)."""
    return twirp("RoomService", "DeleteRoom", {"room": room}, {"roomCreate": True})


def start_room_recording(room: str, filepath: str) -> str:
    """Egress „room composite” do pliku MP4. Miejsce zapisu (S3) zna **serwer egress** z własnej
    konfiguracji – platforma podaje wyłącznie ścieżkę, więc poświadczenia magazynu nie jadą w żądaniu."""
    data = twirp(
        "Egress",
        "StartRoomCompositeEgress",
        {
            "room_name": room,
            "layout": "speaker",
            "file_outputs": [{"file_type": "MP4", "filepath": filepath}],
        },
        {"roomRecord": True},
    )
    return str(data.get("egress_id") or data.get("egressId") or "")


def start_room_stream(room: str, rtmp_url: str) -> str:
    """Egress do RTMP (np. YouTube Live). Adres z kluczem strumienia jedzie tylko w tym żądaniu."""
    data = twirp(
        "Egress",
        "StartRoomCompositeEgress",
        {
            "room_name": room,
            "layout": "speaker",
            "stream_outputs": [{"protocol": "RTMP", "urls": [rtmp_url]}],
        },
        {"roomRecord": True},
    )
    return str(data.get("egress_id") or data.get("egressId") or "")


def stop_egress(egress_id: str) -> dict:
    return twirp("Egress", "StopEgress", {"egress_id": egress_id}, {"roomRecord": True})
