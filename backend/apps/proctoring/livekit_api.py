"""Polecenia LiveKit potrzebne nadzorowi – na kliencie z WEB-01 (``apps.webinars.livekit``).

Podpis tokenów, Twirp, limit czasu, obsługa błędów i jedno wyjście do sieci (``_http_post``,
podmieniane w testach) są w ``apps.webinars.livekit`` – tutaj są wyłącznie **kształty**, których
webinar nie potrzebował:

- token z uprawnieniami nadzoru (:func:`student_grants`, :func:`proctor_grants`) – uczeń nadaje
  wyłącznie wskazane źródła i **nie odbiera** niczego, nadzorujący odbiera, nie nadaje i jest ukryty,
- ``RoomService/SendData`` – wiadomość do jednego ucznia (``destination_identities``),
- ``RoomService/GetParticipant`` – serwer sprawdza, że kamera ucznia naprawdę nadaje,
- ``Egress/StartTrackEgress`` – nagranie surowej ścieżki kamery bez transkodowania.

Nazwy pól i uprawnień – z dokumentacji LiveKit („Authentication”, „Server APIs”, „Egress”);
testy sprawdzają je niezależną implementacją (PyJWT w ``tests/fake_livekit``).
"""

from __future__ import annotations

import base64
import json
import time

from django.conf import settings

from apps.competitions.jitsi_jwt import encode_hs256
from apps.webinars import livekit
from apps.webinars.livekit import LiveKitError, LiveKitUnavailable  # noqa: F401 - eksport dla serwisu

#: Temat wiadomości na kanale danych – konsola ucznia słucha wyłącznie tego tematu.
DATA_TOPIC = "proctoring"

#: Źródło ścieżki w ``TrackInfo.source`` – protojson oddaje nazwę albo liczbę enumu.
SOURCE_CAMERA = ("CAMERA", 1)
SOURCE_SCREEN = ("SCREEN_SHARE", 3)
SOURCE_MICROPHONE = ("MICROPHONE", 2)


def student_grants(room: str, *, screen: bool, microphone: bool) -> dict:
    """Uczeń nadaje kamerę (i ekran / mikrofon, gdy etap ich wymaga) – i **nic nie odbiera**.

    ``canSubscribe=false``: uczeń w pokoju grupy nie zobaczy obrazu innego ucznia, nawet gdyby
    przerobił skrypt strony. ``canPublishData=false``: kanał danych należy do serwera (wiadomości
    nadzorujących idą ``SendData``), więc uczeń nie roześle nic innym uczniom pokoju.
    ``canPublishSources`` zamyka listę nadawanych źródeł – mikrofon tylko, gdy etap go wymaga.
    """
    sources = ["camera"]
    if screen:
        sources.append("screen_share")
    if microphone:
        sources.append("microphone")
    return {
        "room": room,
        "roomJoin": True,
        "canPublish": True,
        "canPublishSources": sources,
        "canSubscribe": False,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
    }


def proctor_grants(room: str) -> dict:
    """Nadzorujący odbiera obraz pokoju swojej grupy, nie nadaje i jest **ukryty** przed uczniami."""
    return {
        "room": room,
        "roomJoin": True,
        "canPublish": False,
        "canSubscribe": True,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
        "hidden": True,
    }


def access_token(*, identity: str, name: str, video: dict, ttl: int | None = None, now=None) -> str:
    """Token wejścia z gotowymi uprawnieniami – ten sam kształt i podpis, co token webinaru."""
    now = int(now if now is not None else time.time())
    ttl = int(ttl if ttl is not None else settings.LIVEKIT_TOKEN_TTL_SECONDS)
    claims = {
        "iss": livekit.api_key(),
        "sub": identity,
        "name": name,
        "nbf": now - livekit.CLOCK_SKEW_SECONDS,
        "exp": now + max(60, ttl),
        "video": video,
    }
    return encode_hs256(claims, livekit.api_secret())


def send_data(room: str, identity: str, payload: dict) -> dict:
    """Wiadomość do **jednego** ucznia (``destination_identities``), niezawodnym kanałem."""
    data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return livekit.twirp(
        "RoomService",
        "SendData",
        {
            "room": room,
            "data": data,
            "kind": "RELIABLE",
            "destination_identities": [identity],
            "topic": DATA_TOPIC,
        },
        {"roomAdmin": True, "room": room},
    )


def get_participant(room: str, identity: str) -> dict:
    """Stan uczestnika w pokoju (ścieżki ze źródłami). ``LiveKitError('not_found')`` – nie ma go."""
    return livekit.twirp(
        "RoomService",
        "GetParticipant",
        {"room": room, "identity": identity},
        {"roomAdmin": True, "room": room},
    )


def _source_is(track: dict, accepted: tuple) -> bool:
    return track.get("source") in accepted


def live_sources(participant: dict) -> set[str]:
    """``{"camera", "screen"}`` – źródła, które uczestnik nadaje i których nie wyciszył."""
    found = set()
    for track in participant.get("tracks") or []:
        if track.get("muted"):
            continue
        if _source_is(track, SOURCE_CAMERA):
            found.add("camera")
        elif _source_is(track, SOURCE_SCREEN):
            found.add("screen")
    return found


def is_camera(track: dict) -> bool:
    return _source_is(track or {}, SOURCE_CAMERA)


def is_screen(track: dict) -> bool:
    return _source_is(track or {}, SOURCE_SCREEN)


def start_track_recording(room: str, track_sid: str, filepath: str) -> str:
    """Track Egress do pliku: surowa ścieżka (WebM/VP8) **bez transkodowania** – tanio dla serwera.

    Kompozycja (``RoomComposite``/``TrackComposite``) dekoduje i koduje obraz – przy kilkuset
    uczniach to kilkaset procesów kodera. Ścieżka kamery jest już mała (320×240, 10 kl./s,
    ≤ 150 kb/s), więc zapisujemy ją tak, jak przyszła. Miejsce zapisu (S3) zna wyłącznie egress.
    """
    data = livekit.twirp(
        "Egress",
        "StartTrackEgress",
        {"room_name": room, "track_id": track_sid, "file": {"filepath": filepath}},
        {"roomRecord": True},
    )
    return str(data.get("egress_id") or data.get("egressId") or "")


def stop_egress(egress_id: str) -> dict:
    return livekit.stop_egress(egress_id)
