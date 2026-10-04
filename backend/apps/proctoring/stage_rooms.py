"""Pokoje rozmów etapu w LiveKit (STAGE-LK-01) – bilet LiveKit dla rozstrzygnięcia ``room_access``.

Etap w formie rozmowy z dostawcą ``livekit`` wchodzi **tymi samymi** widokami wejścia, co pokoje
Jitsi (``apps.web.views.video``), a o tym, kto i kiedy wchodzi, rozstrzyga **ta sama** reguła
(``apps.competitions.room_access``). Ten moduł robi wyłącznie to, czego Jitsi nie potrzebuje:

- zamienia :class:`~apps.competitions.room_access.RoomPass` na token LiveKit (podpis i klucz – klient
  WEB-01, ``apps.webinars.livekit``): moderator Jitsi → ``roomAdmin`` + nadawanie, odbiór i kanał
  danych; uczestnik → nadawanie, odbiór i kanał danych (jak w Jitsi: kamera, mikrofon, czat), bez
  administracji; okno tokenu = okno biletu (``nbf``/``exp``) – dokładnie jak przepustka Jitsi,
- wykonuje polecenia moderatora z pokoju (wyproszenie, odebranie i oddanie głosu) przez API serwera,
- łączy rozmowę z **nadzorem zdalnym** (PROC-01), gdy koordynator włączył go dla etapu: uczeń wchodzi
  na rozmowę dopiero ze zgodą na nadzór i sprawdzonym sprzętem (jak do etapu pisemnego), połączenia
  trafiają do dziennika sesji nadzoru, a przy ``record`` kamera ucznia jest nagrywana (Track Egress)
  z retencją nadzoru. Pokoje Jitsi nie nagrywają (``ENABLE_RECORDING=0``), więc **bez** nadzoru
  pokoje LiveKit też nie – uprawnienia i możliwości obu dostawców są te same.
"""

from __future__ import annotations

import logging
import time

from django.utils.translation import gettext as _

from apps.competitions import room_access
from apps.competitions.jitsi_jwt import encode_hs256
from apps.core.api import DomainError
from apps.webinars import livekit

logger = logging.getLogger(__name__)


def grants(room_pass: room_access.RoomPass) -> dict:
    """Uprawnienia ``video`` tokenu – odwzorowanie ról Jitsi na LiveKit (STAGE-LK-01 § 3)."""
    video = {
        "room": room_access.livekit_room_of(room_pass.url),
        "roomJoin": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }
    if room_pass.moderator:
        video["roomAdmin"] = True
    return video


def access_token(room_pass: room_access.RoomPass, user, *, now=None) -> dict:
    """Token LiveKit na pokój biletu, ważny w oknie biletu. ``identity`` – pseudonim konta (WEB-01)."""
    from apps.webinars.services import pseudonym

    room = room_access.livekit_room_of(room_pass.url)
    if not room or not livekit.configured():
        raise DomainError(_("Serwer wideo nie odpowiada. Spróbuj za chwilę."), "LIVEKIT_UNAVAILABLE", 502)
    now = int(now if now is not None else time.time())
    identity = pseudonym(user)
    claims = {
        "iss": livekit.api_key(),
        "sub": identity,
        "name": room_pass.display_name,
        "nbf": int(room_pass.not_before.timestamp()) - livekit.CLOCK_SKEW_SECONDS,
        "exp": int(room_pass.expires_at.timestamp()),
        "video": grants(room_pass),
    }
    return {
        "url": livekit.ws_url(),
        "token": encode_hs256(claims, livekit.api_secret()),
        "identity": identity,
        "role": "presenter" if room_pass.moderator else "viewer",
    }


# --- nadzór zdalny przy rozmowie ------------------------------------------------------------------


def proctoring_check(stage, participant) -> None:
    """Rozmowa w etapie z nadzorem: zgoda na nadzór, sprawdzony sprzęt (i zdjęcie, gdy wymagane) –
    albo zatwierdzona alternatywa. Bez flagi ``proctoring`` i bez nadzoru etapu – nic (zero zapytań
    przy wyłączonej fladze: ``enabled`` czyta pole konkursu)."""
    from . import services

    if not services.enabled(services.stage_competition(stage)):
        return
    config = services.config_for(stage)
    if config is None:
        return
    session = services.session_for(stage, participant)
    if services.interview_ready(session, config):
        return
    raise DomainError(
        _("Ta rozmowa jest nadzorowana zdalnie. Najpierw wyraź zgodę i sprawdź sprzęt w konsoli nadzoru."),
        "PROCTORING_REQUIRED",
        409,
    )


# --- polecenia moderatora -------------------------------------------------------------------------


def control(room_pass: room_access.RoomPass, action: str, identity: str) -> None:
    """Polecenie moderatora: ``remove`` (wyproś), ``listener`` (odbierz głos), ``speaker`` (oddaj).

    Tylko z biletem moderatora **tego** pokoju – to samo, co może moderator w Jitsi (wyproszenie,
    wyciszenie). Identyfikator musi być pseudonimem konta (``u-…``).
    """
    from apps.webinars.services import IDENTITY

    if not room_pass.moderator:
        raise DomainError("Nie masz uprawnień moderatora w tym pokoju.", "ROOM_NOT_MODERATOR", 403)
    if not IDENTITY.match(identity or ""):
        raise DomainError(_("Tej osoby nie ma teraz w pokoju."), "ROOM_NOT_IN_ROOM", 409)
    room = room_access.livekit_room_of(room_pass.url)
    try:
        if action == "remove":
            livekit.remove_participant(room, identity)
        elif action in ("speaker", "listener"):
            livekit.set_can_publish(room, identity, action == "speaker")
        else:
            raise DomainError("Nieznana czynność.", "UNKNOWN_ACTION", 400)
    except livekit.LiveKitUnavailable as exc:
        raise DomainError(
            _("Serwer wideo nie odpowiada. Spróbuj za chwilę."), "LIVEKIT_UNAVAILABLE", 502
        ) from exc
    except livekit.LiveKitError as exc:
        raise DomainError(_("Tej osoby nie ma teraz w pokoju."), "ROOM_NOT_IN_ROOM", 409) from exc


# --- webhooki pokoi rozmów (dla nadzoru) ----------------------------------------------------------


def stage_for_room(name: str):
    """Etap i zapis po nazwie pokoju rozmowy LiveKit (``livekit://<pokój>`` przy zapisie albo terminie)."""
    from apps.competitions.models import InterviewBooking, InterviewSlot

    url = f"{room_access.LIVEKIT_SCHEME}{name}"
    booking = InterviewBooking._base_manager.select_related("slot__stage__edition__competition").filter(
        meeting_url=url
    )
    first = booking.first()
    if first is not None:
        return first.slot.stage
    slot = (
        InterviewSlot._base_manager.select_related("stage__edition__competition")
        .filter(meeting_url=url)
        .first()
    )
    return slot.stage if slot is not None else None
