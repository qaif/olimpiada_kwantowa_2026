"""Pokoje rozmów etapu w LiveKit (STAGE-LK-01) – bilet LiveKit dla rozstrzygnięcia ``room_access``.

Etap w formie rozmowy z dostawcą ``livekit`` wchodzi **tymi samymi** widokami wejścia, co pokoje
Jitsi (``apps.web.views.video``), a o tym, kto i kiedy wchodzi, rozstrzyga **ta sama** reguła
(``apps.competitions.room_access``). Ten moduł robi wyłącznie to, czego Jitsi nie potrzebuje:

- zamienia :class:`~apps.competitions.room_access.RoomPass` na token LiveKit (podpis i klucz – klient
  WEB-01, ``apps.webinars.livekit``): nadawanie, odbiór i kanał danych dla każdej roli (jak w Jitsi:
  kamera, mikrofon, czat) – **bez ``roomAdmin``** także dla moderatora, bo jego polecenia idą przez
  platformę; okno tokenu = okno biletu (``nbf``/``exp``) – dokładnie jak przepustka Jitsi; przed
  tokenem ``CreateRoom`` (serwer ma ``auto_create: false``),
- wykonuje polecenia moderatora z pokoju (wyproszenie, odebranie i oddanie głosu, ponowne wpuszczenie)
  przez API serwera i zapisuje je przy terminie, żeby odświeżenie strony ich nie cofało,
- łączy rozmowę z **nadzorem zdalnym** (PROC-01), gdy koordynator włączył go dla etapu: uczeń wchodzi
  na rozmowę dopiero ze zgodą na nadzór i sprawdzonym sprzętem (jak do etapu pisemnego), połączenia
  trafiają do dziennika sesji nadzoru, a przy ``record`` kamera ucznia **ze zgodą** jest nagrywana
  (Track Egress) z retencją nadzoru. Pokoje Jitsi nie nagrywają (``ENABLE_RECORDING=0``), więc **bez** nadzoru
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


#: Pusty pokój rozmowy znika po tylu sekundach (``CreateRoom.empty_timeout``) – kwadrans wystarcza
#: na odświeżenie strony i powrót po zerwanym połączeniu w trakcie rozmowy.
ROOM_EMPTY_TIMEOUT_SECONDS = 900


def _unavailable() -> DomainError:
    return DomainError(_("Serwer wideo nie odpowiada. Spróbuj za chwilę."), "LIVEKIT_UNAVAILABLE", 502)


def room_of(room_pass: room_access.RoomPass, user) -> str:
    """Pokój LiveKit biletu. Próba sprzętu – **osobny pokój** na zapis ucznia (``…-b<zapis>-test``)
    albo na osobę z komisji (``…-s<konto>-test``): wspólny ``…-test`` terminu byłby miejscem, w którym
    uczniowie jednego terminu spotykają się bez nadzoru i bez moderatora (przegląd L-5)."""
    room = room_access.livekit_room_of(room_pass.url)
    if not room or room_pass.kind != room_access.PRECHECK:
        return room
    from apps.competitions.video import PRECHECK_SUFFIX

    base = room[: -len(PRECHECK_SUFFIX)] if room.endswith(PRECHECK_SUFFIX) else room
    owner = f"b{room_pass.booking_id}" if room_pass.booking_id else f"s{user.pk}"
    return f"{base}-{owner}{PRECHECK_SUFFIX}"


def grants(room_pass: room_access.RoomPass, room: str = "", *, can_publish: bool = True) -> dict:
    """Uprawnienia ``video`` tokenu (STAGE-LK-01 § 3). **Bez ``roomAdmin``** także dla moderatora
    (przegląd L-1): polecenia moderatora idą przez platformę (``control``), więc przeglądarka nie
    potrzebuje uprawnień administratora pokoju – skradziony token moderatora niczego nie wyprosi.
    ``can_publish=False`` – uczeń, któremu moderator odebrał głos (decyzja przeżywa ponowne wejście)."""
    return {
        "room": room or room_access.livekit_room_of(room_pass.url),
        "roomJoin": True,
        "canPublish": bool(can_publish),
        "canSubscribe": True,
        "canPublishData": True,
    }


def _block_for(slot, identity: str):
    from .models import InterviewRoomBlock

    if slot is None:
        return None
    return InterviewRoomBlock.objects.filter(slot=slot, identity=identity).first()


def access_token(room_pass: room_access.RoomPass, user, *, slot=None, now=None) -> dict:
    """Token LiveKit na pokój biletu, ważny w oknie biletu. ``identity`` – pseudonim konta (WEB-01).

    Kolejność: serwer skonfigurowany → decyzje moderatora dla ucznia (usunięty – odmowa, bez głosu –
    token bez nadawania) → ``CreateRoom`` (serwer ma ``auto_create: false``, przegląd H-1) → token.
    """
    from apps.webinars.services import pseudonym

    from .models import RoomBlockKind

    room = room_of(room_pass, user)
    if not room or not livekit.configured():
        raise _unavailable()
    identity = pseudonym(user)
    can_publish = True
    if room_pass.role == room_access.ROLE_PARTICIPANT and room_pass.kind == room_access.INTERVIEW:
        block = _block_for(slot, identity)
        if block is not None and block.kind == RoomBlockKind.REMOVED:
            raise DomainError(
                _("Moderator usunął Cię z tej rozmowy. Skontaktuj się z organizatorem."), "ROOM_REMOVED", 403
            )
        can_publish = block is None
    try:
        livekit.create_room(room, empty_timeout=ROOM_EMPTY_TIMEOUT_SECONDS)
    except (livekit.LiveKitUnavailable, livekit.LiveKitError) as exc:
        raise _unavailable() from exc
    now = int(now if now is not None else time.time())
    claims = {
        "iss": livekit.api_key(),
        "sub": identity,
        "name": room_pass.display_name,
        "nbf": int(room_pass.not_before.timestamp()) - livekit.CLOCK_SKEW_SECONDS,
        "exp": int(room_pass.expires_at.timestamp()),
        "video": grants(room_pass, room, can_publish=can_publish),
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
    session = services.session_for(stage, participant, create=False)
    if services.interview_ready(session, config):
        return
    raise DomainError(
        _("Ta rozmowa jest nadzorowana zdalnie. Najpierw wyraź zgodę i sprawdź sprzęt w konsoli nadzoru."),
        "PROCTORING_REQUIRED",
        409,
    )


# --- polecenia moderatora -------------------------------------------------------------------------


CONTROL_ACTIONS = ("remove", "listener", "speaker", "readmit")


def _label(slot, identity: str) -> str:
    """„Imię N.” ucznia **tego** terminu o tym pseudonimie – pętla po zapisach jednego terminu
    (kilka osób), nie po etapie."""
    from apps.competitions.jitsi_jwt import short_name
    from apps.webinars.services import pseudonym

    for booking in slot.bookings.select_related("entry__participant__user"):
        participant = booking.entry.participant
        if participant is not None and pseudonym(participant.user) == identity:
            return short_name(participant.user) or participant.public_code
    return ""


def control(room_pass: room_access.RoomPass, action: str, identity: str, *, slot=None, actor=None) -> None:
    """Polecenie moderatora: ``remove`` (wyproś), ``listener`` (odbierz głos), ``speaker`` (oddaj),
    ``readmit`` (wpuść ponownie).

    Tylko z biletem moderatora **tego** pokoju – to samo, co może moderator w Jitsi (wyproszenie,
    wyciszenie). Identyfikator musi być pseudonimem konta (``u-…``). Wyproszenie i odebranie głosu
    zostają zapisane przy terminie (``InterviewRoomBlock``), więc odświeżenie strony ich nie cofa
    (przegląd L-4); „wpuść ponownie” i „oddaj głos” je zdejmują.
    """
    from apps.webinars.services import IDENTITY

    from .models import InterviewRoomBlock, RoomBlockKind

    if not room_pass.moderator:
        raise DomainError("Nie masz uprawnień moderatora w tym pokoju.", "ROOM_NOT_MODERATOR", 403)
    if action not in CONTROL_ACTIONS:
        raise DomainError("Nieznana czynność.", "UNKNOWN_ACTION", 400)
    if not IDENTITY.match(identity or ""):
        raise DomainError(_("Tej osoby nie ma teraz w pokoju."), "ROOM_NOT_IN_ROOM", 409)
    room = room_access.livekit_room_of(room_pass.url)
    if slot is not None and action in ("remove", "listener"):
        kind = RoomBlockKind.REMOVED if action == "remove" else RoomBlockKind.MUTED
        existing = InterviewRoomBlock.objects.filter(slot=slot, identity=identity).first()
        if existing is None:
            InterviewRoomBlock.objects.create(
                slot=slot,
                identity=identity,
                kind=kind,
                label=_label(slot, identity)[:80],
                created_by=actor if getattr(actor, "pk", None) else None,
            )
        elif kind == RoomBlockKind.REMOVED and existing.kind != kind:
            existing.kind = kind
            existing.save(update_fields=["kind"])
    elif action == "readmit":
        if slot is not None:
            InterviewRoomBlock.objects.filter(slot=slot, identity=identity).delete()
        return
    elif slot is not None and action == "speaker":
        InterviewRoomBlock.objects.filter(slot=slot, identity=identity, kind=RoomBlockKind.MUTED).delete()
    try:
        if action == "remove":
            livekit.remove_participant(room, identity)
        else:
            livekit.set_can_publish(room, identity, action == "speaker")
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError as exc:
        if action == "remove":
            return  # osoby już nie ma w pokoju – decyzja i tak zapisana: nie wejdzie z powrotem
        raise DomainError(_("Tej osoby nie ma teraz w pokoju."), "ROOM_NOT_IN_ROOM", 409) from exc


def blocks_for(slot) -> list:
    """Decyzje moderatorów terminu – lista na stronie pokoju moderatora („Wpuść ponownie”, „Oddaj głos”)."""
    from .models import InterviewRoomBlock

    if slot is None:
        return []
    return list(InterviewRoomBlock.objects.filter(slot=slot).order_by("created_at", "pk"))


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
