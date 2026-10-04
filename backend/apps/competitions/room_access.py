"""Kto, kiedy i z jaką rolą wchodzi do pokoju rozmowy etapu – **niezależnie od dostawcy wideo**.

Etap w formie rozmowy (drugi etap Olimpiady Kwantowej) ma pokój na terminie. Od v0.39.0 pokój stoi
na naszym Jitsi z przepustkami (``jitsi_jwt``), od STAGE-LK-01 koordynator może wybrać dla etapu
**LiveKit** (``VideoProvider.LIVEKIT``). Uprawnienia mają być w obu dostawcach **dokładnie te
same** – dlatego reguła „kto wchodzi” mieszka tutaj, raz, a dostawcy dostają gotowe rozstrzygnięcie
(:class:`RoomPass`) i zamieniają je wyłącznie na swój bilet: Jitsi – JWT Prosody we fragmencie
adresu (``apps.web.views.video``), LiveKit – token dostępu z uprawnieniami pokoju
(``apps.proctoring.stage_rooms``).

Reguły (te same, które od v0.39.0 stały w widokach Jitsi – przeniesione bez zmiany zachowania):

- **uczestnik** – wyłącznie własny zapis w etapie tego konkursu, niezdyskwalifikowany; rozmowa
  w oknie ``interview_window`` terminu, bez moderatora; próba sprzętu (pokój ``…-test``) – do końca
  okna, na ``precheck_lifetime``,
- **koordynator** (ekran terminów) i **komisja** (aktywny członek komisji konkursu) – dowolny
  termin tego konkursu, rozmowa w tym samym oknie co uczestnik **jako moderator**; próba sprzętu
  bez okna i bez moderatora,
- nazwa w pokoju – „Imię N.” (komisja bez imienia: „Komisja”).

Bramki ról (mixiny widoków) i zawężone querysety zostają w widokach – są wspólne dla obu dostawców,
bo oba wchodzą tymi samymi adresami wejścia (``interview-join``, ``…-slot-join``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

INTERVIEW = "interview"
PRECHECK = "precheck"
ROLE_PARTICIPANT = "participant"
ROLE_COORDINATOR = "coordinator"
ROLE_COMMITTEE = "committee"

#: Adres pokoju LiveKit zapisany przy terminie/zapisie (``livekit://<pokój>``). Własny schemat,
#: a nie adres strony: pokój nie ma adresu, do którego dałoby się wejść bez platformy – wchodzi się
#: wyłącznie widokami wejścia, a ten napis jest tylko identyfikatorem pokoju w polu ``meeting_url``.
LIVEKIT_SCHEME = "livekit://"


class RoomNotYet(Exception):
    """Pokój jeszcze zamknięty – ``opens_at`` mówi, od kiedy."""

    def __init__(self, opens_at):
        super().__init__(opens_at)
        self.opens_at = opens_at


class RoomOver(Exception):
    """Okno pokoju minęło."""


@dataclass(frozen=True)
class RoomPass:
    """Rozstrzygnięcie „ta osoba wchodzi teraz do tego pokoju” – wspólne dla dostawców."""

    url: str  # adres pokoju (z ``-test`` dla próby sprzętu)
    kind: str  # ``interview`` / ``precheck``
    role: str  # ``participant`` / ``coordinator`` / ``committee``
    moderator: bool
    display_name: str
    not_before: datetime
    expires_at: datetime
    #: Zapis uczestnika (bilet uczestnika) – LiveKit robi z niego **osobny** pokój próby sprzętu
    #: na zapis, żeby uczniowie jednego terminu nie spotykali się bez nadzoru w ``…-test``.
    booking_id: int | None = None


# --- dostawca pokoju ------------------------------------------------------------------------------


def livekit_room_of(url: str) -> str:
    """Nazwa pokoju LiveKit z ``livekit://<pokój>`` albo ``""`` – ta sama składnia nazwy, co Jitsi."""
    from .jitsi_jwt import _ROOM_PATTERN

    value = (url or "").strip()
    if not value.startswith(LIVEKIT_SCHEME):
        return ""
    room = value[len(LIVEKIT_SCHEME) :].lower()
    return room if _ROOM_PATTERN.fullmatch(room) else ""


def is_livekit_room(url: str) -> bool:
    """Pokój LiveKit etapu – **niezależnie od konfiguracji serwera** (przegląd STAGE-LK-01, M-3).

    ``livekit://…`` nie jest adresem, który dałoby się otworzyć: gdyby po wyłączeniu serwera ekrany
    i listy uznały go za „zwykły link”, uczeń dostałby do kliknięcia napis ``livekit://…``. Pokój
    zostaje więc pokojem platformy, a brak serwera kończy się przy tokenie – 502 „Serwer wideo nie
    odpowiada” (``apps.proctoring.stage_rooms.access_token``).
    """
    return bool(livekit_room_of(url))


def is_platform_room(url: str) -> bool:
    """Czy do pokoju wchodzi się **przez platformę** (przepustka Jitsi albo token LiveKit), a nie linkiem.

    Dla adresów Jitsi to dokładnie ``jitsi_jwt.is_platform_room`` (zachowanie sprzed STAGE-LK-01);
    pokój LiveKit (``livekit://…``) dochodzi jako drugi przypadek.
    """
    from .jitsi_jwt import is_platform_room as jitsi_platform_room

    return jitsi_platform_room(url) or is_livekit_room(url)


def provider_of(url: str) -> str:
    """``livekit`` / ``jitsi`` / ``""`` – który dostawca wystawia bilet do tego pokoju."""
    if is_livekit_room(url):
        return "livekit"
    from .jitsi_jwt import is_platform_room as jitsi_platform_room

    return "jitsi" if jitsi_platform_room(url) else ""


# --- reguły wejścia -------------------------------------------------------------------------------


def participant_booking(stage, participant):
    """Własny, niezdyskwalifikowany zapis uczestnika w etapie i adres pokoju – albo ``None``.

    ``None`` znaczy dla widoku 404: zapisu nie ma, uczestnik zdyskwalifikowany albo pokój nie jest
    pokojem platformy (pokazujemy wtedy zwykły link, jak przed v0.39.0).
    """
    from .interviews import booking_for_participant
    from .models import StageEntryStatus
    from .video import booking_meeting_url

    booking = booking_for_participant(stage, participant)
    if booking is None or booking.entry.status == StageEntryStatus.DISQUALIFIED:
        return None
    url = booking_meeting_url(booking)
    if not is_platform_room(url):
        return None
    return booking, url


def participant_pass(booking, url: str, user, *, kind: str, now=None) -> RoomPass:
    """Bilet uczestnika: rozmowa w oknie terminu, próba sprzętu do końca okna. Bez moderatora."""
    from .jitsi_jwt import interview_window, precheck_lifetime, short_name
    from .video import precheck_url

    now = now or timezone.now()
    opens_at, closes_at = interview_window(booking.slot)
    if kind == PRECHECK:
        if now >= closes_at:
            raise RoomOver()
        return RoomPass(
            url=precheck_url(url),
            kind=PRECHECK,
            role=ROLE_PARTICIPANT,
            moderator=False,
            display_name=short_name(user),
            not_before=now,
            expires_at=now + precheck_lifetime(),
            booking_id=booking.pk,
        )
    if now < opens_at:
        raise RoomNotYet(opens_at)
    if now >= closes_at:
        raise RoomOver()
    return RoomPass(
        url=url,
        kind=INTERVIEW,
        role=ROLE_PARTICIPANT,
        moderator=False,
        display_name=short_name(user),
        not_before=opens_at,
        expires_at=closes_at,
        booking_id=booking.pk,
    )


def slot_room_url(slot) -> str:
    """Adres pokoju terminu, jeśli to pokój platformy – inaczej ``""`` (widok odpowie 404)."""
    from .video import slot_meeting_url

    url = slot_meeting_url(slot)
    return url if is_platform_room(url) else ""


def staff_pass(slot, url: str, user, *, role: str, kind: str, now=None) -> RoomPass:
    """Bilet koordynatora albo komisji: rozmowa jako moderator w oknie terminu; próba – bez okna."""
    from .jitsi_jwt import interview_window, precheck_lifetime, short_name
    from .video import PRECHECK_SUFFIX

    now = now or timezone.now()
    name = short_name(user) or "Komisja"
    if kind == PRECHECK:
        return RoomPass(
            url=f"{url}{PRECHECK_SUFFIX}",
            kind=PRECHECK,
            role=role,
            moderator=False,
            display_name=name,
            not_before=now,
            expires_at=now + precheck_lifetime(),
        )
    opens_at, closes_at = interview_window(slot)
    if now < opens_at:
        raise RoomNotYet(opens_at)
    if now >= closes_at:
        raise RoomOver()
    return RoomPass(
        url=url,
        kind=INTERVIEW,
        role=role,
        moderator=True,
        display_name=name,
        not_before=opens_at,
        expires_at=closes_at,
    )
