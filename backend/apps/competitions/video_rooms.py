"""Pokoje wideo poza terminami rozmów: zebrania komisji, konsultacje, gość bez konta (v0.39.0).

Rozmowy kwalifikacyjne mają swoje pokoje i krótkie przepustki (``apps.competitions.video``,
``apps.competitions.jitsi_jwt``). Organizator potrzebuje jednak też pokoju **bez terminu**: komisja
umawia się na naradę, koordynator konsultuje zadanie z autorem spoza platformy. Od chwili, w której
własne Jitsi przyjmuje wyłącznie przepustki, taki pokój musi powstać tutaj.

Model i czynności w jednym pliku (jak ``apps.competitions.logistics``): czyta się je wyłącznie
razem, a ``models.py`` rejestruje model jedną linią importu na końcu.

**Dwie drogi wejścia – obie przez platformę, obie z krótką przepustką:**

1. **link-zaproszenie** (wariant gospodarza i wariant gościa) – dla osób bez konta. To jest adres
   **na platformie** (``/zaproszenie/wideo/<klucz>/``), a nie na Jitsi, i nie niesie żadnej
   przepustki. Bramka (``apps.web.views.video.VideoGatewayView``) przy każdym wejściu sprawdza,
   czy pokój istnieje, nie jest zamknięty i nie wygasł, prosi gościa o nazwę wyświetlaną i dopiero
   na ``POST`` „Dołącz” wystawia przepustkę na kilka minut (``JITSI_JWT_GATEWAY_MINUTES``).
   Skutek: **zamknięcie pokoju, jego wygaśnięcie albo wymiana linku na nowy działa od razu** dla
   każdego, kto jeszcze nie wszedł. Kto już siedzi w trwającej rozmowie, zostaje do jej opuszczenia
   (Jitsi nie pyta platformy drugi raz) – a po zerwaniu łącza wraca tylko przez bramkę, czyli tylko
   wtedy, gdy pokój jest nadal otwarty. Długich tokenów JWT nie ma nigdzie,
2. **wejście z panelu** („Dołącz”) – dla członków komisji tego konkursu, gdy pokój jest im
   udostępniony, oraz dla koordynatora i autora pokoju. Krótka przepustka
   (``JITSI_JWT_SESSION_MINUTES``, nie dłużej niż ważność pokoju).

**Klucze linków są identyfikatorami, nie tokenami** – ale traktujemy je jak poświadczenie.
Kolumny ``host_key`` i ``guest_key`` trzymają je **jawnie** (192 bity z ``secrets``), bo właściciel
chce, żeby koordynator mógł zobaczyć linki także później („Pokaż linki”); skrót SHA-256 dawałby
wyłącznie pokazanie raz. W zamian: klucz nigdy nie trafia do audytu (``diff`` niesie skrót nazwy
pokoju i nazwę wariantu), do logu ani do eksportu, a pokazanie linków jest osobną czynnością
z wpisem ``video.room_links_viewed`` i odpowiedzią ``no-store``. Wyciekły link wymienia się
jednym przyciskiem („Wygeneruj nowy link”, :func:`rotate_link`) bez zamykania pokoju.

**Kto zakłada pokoje:** koordynator tego konkursu – zawsze, i członek komisji (recenzent
albo komisja odwoławcza **tego** konkursu, status ACTIVE) z uprawnieniem
``CommitteeMember.video_room_issuer``, które nadaje i odbiera koordynator. Członek komisji widzi
i zamyka wyłącznie **własne** pokoje; koordynator – wszystkie pokoje konkursu.

**Odebranie uprawnienia** (albo zawieszenie członka) od razu zamyka mu ekran pokoi i wejście
z panelu do pokoi, które założył – także innym członkom komisji. Linków-zaproszeń jego pokoi
**nie** wyłącza automatycznie: to decyzja koordynatora (goście mogą być umówieni), podejmowana
jednym przyciskiem obok odebrania uprawnienia („Zamknij pokoje tej osoby”).

**Nazwa pokoju** powstaje tu, nie u człowieka: ``olimpiada-<etykieta>-<6 losowych znaków>``. Pełnej
nazwy nie da się wpisać, bo wtedy przepustka do takiego pokoju mogłaby otwierać pokój rozmowy
kwalifikacyjnej (nazwa jest jedynym, co claim ``room`` porównuje).

Danych osobowych model nie niesie poza ``created_by`` (kto założył – potrzebne koordynatorowi
i do reguły „własne pokoje”). Retencja: wiersze są danymi operacyjnymi; ``created_by`` znika
(``SET_NULL``) razem z kontem.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError

from .jitsi_jwt import gateway_lifetime, issue, join_url, jwt_enabled, room_url, session_lifetime, short_name
from .scoping import competition_scoped_manager
from .video import random_suffix, slugify_ascii

logger = logging.getLogger(__name__)

#: Zamknięta lista ważności pokoju (dni). Lista, a nie pole liczbowe: ważność pokoju jest
#: zarazem ważnością jego linków-zaproszeń, a cztery wartości da się opisać jednym zdaniem
#: w podręczniku. Górną granicę przycinają ``JITSI_JWT_ROOM_MAX_DAYS`` (koordynator, domyślnie
#: 60 – lista 1/7/30/60) i ``JITSI_JWT_COMMITTEE_ROOM_MAX_DAYS`` (komisja, domyślnie 30 – lista
#: 1/7/30); decyzja właściciela z 2.10.2026.
VALIDITY_DAYS = (1, 7, 30, 60)

#: Najdłuższa etykieta pokoju. Do nazwy pokoju wchodzi z niej i tak najwyżej 24 znaki.
LABEL_MAX_LENGTH = 80

#: Bajty losowości klucza linku: 24 bajty = 192 bity, ``token_urlsafe`` daje 32 znaki.
KEY_BYTES = 24

#: Najdłuższa nazwa wyświetlana gościa. Jitsi przyjąłby więcej, ale nazwa stoi w kafelku obrazu
#: i na liście uczestników, a dłuższa niż kilka słów jest już treścią, nie podpisem.
DISPLAY_NAME_MAX_LENGTH = 40

#: Warianty linku. Wartość idzie do audytu (która połowa pary została użyta albo wymieniona).
HOST = "host"
GUEST = "guest"
VARIANTS = (HOST, GUEST)

_WHITESPACE = re.compile(r"\s+")


class RoomCreator(models.TextChoices):
    """W jakiej roli założono pokój – od tego zależy, czy panel nadal wpuszcza do niego komisję."""

    COORDINATOR = "coordinator", "koordynator"
    COMMITTEE = "committee", "członek komisji"


def new_key() -> str:
    """Klucz linku-zaproszenia: 192 bity z ``secrets`` – identyfikator nie do zgadnięcia."""
    return secrets.token_urlsafe(KEY_BYTES)


class VideoRoom(models.Model):
    """Pokój wideo konkursu bez terminu rozmowy. Bez tokenów – te powstają przy każdym wejściu.

    ``host_key`` / ``guest_key`` – klucze dwóch linków-zaproszeń. Przechowywane jawnie (decyzja
    właściciela: koordynator ma widzieć linki także później), więc kolumny są **poświadczeniem**:
    nie trafiają do audytu, logów, eksportów ani do ``__str__``. Wymiana klucza (``rotate_link``)
    unieważnia stary link od razu.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="video_rooms",
        verbose_name="konkurs",
    )
    label = models.CharField("etykieta", max_length=LABEL_MAX_LENGTH)
    room_name = models.CharField("nazwa pokoju", max_length=128, unique=True)
    host_key = models.CharField("klucz linku gospodarza", max_length=64, unique=True, default=new_key)
    guest_key = models.CharField("klucz linku gościa", max_length=64, unique=True, default=new_key)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_rooms_created",
        verbose_name="założył",
    )
    created_as = models.CharField(
        "rola zakładającego", max_length=16, choices=RoomCreator.choices, default=RoomCreator.COORDINATOR
    )
    created_at = models.DateTimeField("założony", default=timezone.now)
    expires_at = models.DateTimeField("ważny do")
    #: Pokój widoczny w panelu członków komisji tego konkursu (recenzenci, komisja odwoławcza).
    committee_access = models.BooleanField("dostępny dla członków komisji", default=False)
    #: Członkowie komisji wchodzą z panelu jako gospodarze (moderatorzy), a nie jako uczestnicy.
    committee_as_moderator = models.BooleanField("komisja jako gospodarze", default=False)
    #: Zamknięty przez koordynatora albo autora: od tej chwili ani panel, ani bramka linku nie
    #: wystawiają do niego żadnej przepustki.
    closed_at = models.DateTimeField("zamknięty", null=True, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "pokój wideo"
        verbose_name_plural = "pokoje wideo"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.label} ({self.room_name})"

    def is_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.closed_at is None and self.expires_at > now

    def state(self, now=None) -> str:
        """``active`` / ``closed`` / ``expired`` – do listy na ekranie."""
        if self.closed_at is not None:
            return "closed"
        return "active" if self.expires_at > (now or timezone.now()) else "expired"

    @property
    def url(self) -> str:
        return room_url(self.room_name)


# --- pomocnicze ---------------------------------------------------------------------------------


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, status.HTTP_400_BAD_REQUEST)


def room_digest(room_name: str) -> str:
    """Krótki skrót nazwy pokoju do audytu – wpis ma dać się połączyć z pokojem, ale nie być adresem."""
    return hashlib.sha256(room_name.encode("utf-8")).hexdigest()[:12]


def validity_choices(*, committee: bool) -> tuple[int, ...]:
    """Ważności dostępne dla roli. Limit komisji nigdy nie przekracza limitu koordynatora."""
    cap = max(1, int(settings.JITSI_JWT_ROOM_MAX_DAYS))
    if committee:
        cap = min(cap, max(1, int(settings.JITSI_JWT_COMMITTEE_ROOM_MAX_DAYS)))
    return tuple(days for days in VALIDITY_DAYS if days <= cap)


def clean_display_name(raw: str) -> str:
    """Nazwa gościa do claimu ``context.user.name``: bez znaków sterujących, jedna spacja, ≤ 40 znaków.

    Nazwę widzą wszyscy w pokoju, a Jitsi wyświetla ją w kilku miejscach interfejsu – znaki
    sterujące (w tym kierunkowe ``U+202E``) i niewidoczne pozwalałyby podszyć się pod kogoś
    z komisji albo rozbić układ listy uczestników. Escapowanie HTML-a robi szablon (tu nic nie
    trafia do HTML-a), a token jest JSON-em – tu chodzi wyłącznie o treść.
    """
    text = unicodedata.normalize("NFC", raw or "")
    text = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:DISPLAY_NAME_MAX_LENGTH].strip()


def committee_member(user, competition):
    """Profil członka komisji **tego** konkursu: aktywny recenzent albo członek komisji odwoławczej.

    Te same funkcje, co bramki paneli recenzenta i komisji (``active_reviewer_profile``,
    ``appeals_committee_profile`` + rola ``appeals`` z ``has_role``) – profil z innego konkursu,
    zawieszony albo czekający na zatwierdzenie nie przechodzi.
    """
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import active_reviewer_profile, has_role
    from apps.appeals.services import appeals_committee_profile

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    member = active_reviewer_profile(user, competition)
    if member is not None:
        return member
    if has_role(user, competition, CompetitionRole.APPEALS):
        return appeals_committee_profile(user, competition)
    return None


def room_issuer(user, competition):
    """Członek komisji, któremu wolno zakładać pokoje (uprawnienie od koordynatora), albo ``None``."""
    member = committee_member(user, competition)
    return member if member is not None and member.video_room_issuer else None


def creator_still_allowed(room: VideoRoom) -> bool:
    """Wejście **z panelu** do pokoju członka komisji działa tylko, póki autor ma uprawnienie.

    Odebranie uprawnienia albo zawieszenie autora zamyka wejście z panelu do wszystkich jego pokoi
    od razu. Linków-zaproszeń ta reguła nie dotyczy (docstring modułu) – te zamyka koordynator.
    """
    if room.created_as != RoomCreator.COMMITTEE:
        return True
    if room.created_by is None:
        return False
    return room_issuer(room.created_by, room.competition) is not None


def panel_joinable(room: VideoRoom, now=None) -> bool:
    """Czy panel wystawi dziś przepustkę do tego pokoju."""
    return jwt_enabled() and room.is_open(now) and creator_still_allowed(room)


def unique_room_name(label: str) -> str:
    """``olimpiada-<etykieta>-<losowe>`` – ten sam schemat, co pokoje rozmów, z etykietą zamiast edycji."""
    for _attempt in range(5):
        name = "-".join(["olimpiada", slugify_ascii(label, max_length=24), random_suffix()])
        if not VideoRoom._base_manager.filter(room_name=name).exists():
            return name
    raise DomainError("Nie udało się wylosować nazwy pokoju – spróbuj jeszcze raz.", "ROOM_NAME_BUSY", 409)


def _audit(actor, action: str, room: VideoRoom, extra: dict | None = None, request=None) -> None:
    from apps.core.models import audit

    audit(actor, action, room, {"room": room_digest(room.room_name), **(extra or {})}, request=request)


# --- czynności ----------------------------------------------------------------------------------


def create_room(
    competition,
    actor,
    *,
    label: str,
    validity_days: int,
    committee_access: bool = False,
    committee_as_moderator: bool = False,
    created_as: str = RoomCreator.COORDINATOR,
    request=None,
) -> VideoRoom:
    """Zakłada pokój z parą kluczy linków-zaproszeń.

    Uprawnienie wołającego (koordynator albo członek komisji z ``video_room_issuer``) sprawdza
    widok – tutaj zostaje reguła, której nie wolno pominąć niezależnie od ekranu: ważność
    z zamkniętej listy dla roli.
    """
    if not jwt_enabled():
        raise DomainError("Przepustki do Jitsi są wyłączone.", "VIDEO_DISABLED", status.HTTP_409_CONFLICT)
    committee = created_as == RoomCreator.COMMITTEE
    try:
        days = int(validity_days)
    except TypeError, ValueError:
        days = 0
    if days not in validity_choices(committee=committee):
        allowed = ", ".join(str(item) for item in validity_choices(committee=committee))
        raise _bad_request(
            f"Ważność pokoju: wybierz jedną z wartości ({allowed} dni).", "ROOM_VALIDITY_INVALID"
        )
    label = (label or "").strip()
    if not label:
        raise _bad_request("Podaj krótką etykietę pokoju.", "ROOM_LABEL_REQUIRED")
    if len(label) > LABEL_MAX_LENGTH:
        raise _bad_request(f"Etykieta może mieć najwyżej {LABEL_MAX_LENGTH} znaków.", "ROOM_LABEL_TOO_LONG")
    if not committee_access:
        committee_as_moderator = False

    now = timezone.now()
    try:
        with transaction.atomic():
            room = VideoRoom.objects.create(
                competition=competition,
                label=label,
                room_name=unique_room_name(label),
                created_by=actor if getattr(actor, "is_authenticated", False) else None,
                created_as=created_as,
                created_at=now,
                expires_at=now + timedelta(days=days),
                committee_access=bool(committee_access),
                committee_as_moderator=bool(committee_as_moderator),
            )
    except IntegrityError as exc:  # wyścig dwóch identycznych losowań – praktycznie niemożliwy
        raise DomainError(
            "Nie udało się założyć pokoju – spróbuj jeszcze raz.", "ROOM_NAME_BUSY", 409
        ) from exc

    # W ``diff`` wyłącznie liczby, flagi i skrót nazwy – bez etykiety (bywa nazwiskiem gościa)
    # i oczywiście bez kluczy linków.
    _audit(
        actor,
        "video.room_created",
        room,
        {
            "validity_days": days,
            "created_as": created_as,
            "committee_access": room.committee_access,
            "committee_as_moderator": room.committee_as_moderator,
        },
        request=request,
    )
    logger.info("Pokój wideo %s: założony (%s dni, %s).", room.pk, days, created_as)
    return room


def gateway_links(room: VideoRoom, *, request=None) -> dict[str, str] | None:
    """Bezwzględne adresy obu linków-zaproszeń albo ``None`` dla pokoju zamkniętego/wygasłego.

    Adres przez ``reverse`` + ``absolute_url``: w konkursie pod prefiksem ścieżki link musi nieść
    prefiks, inaczej zaprowadziłby gościa do konkursu-gospodarza.
    """
    if not jwt_enabled() or not room.is_open():
        return None
    from django.urls import reverse

    from apps.accounts.activation import absolute_url

    def link(key: str) -> str:
        return absolute_url(
            reverse("web:video-gateway", args=[key]), request=request, competition=room.competition
        )

    return {HOST: link(room.host_key), GUEST: link(room.guest_key)}


def view_room_links(room: VideoRoom, actor, *, request=None) -> dict[str, str] | None:
    """Linki do pokazania – z wpisem ``video.room_links_viewed`` (kto, który pokój; bez kluczy).

    Pokazanie linku to dostęp do poświadczenia, więc zostawia ślad tak samo jak jego wymiana.
    Pokój zamknięty albo wygasły nie ma czego pokazać i wpisu nie zostawia.
    """
    links = gateway_links(room, request=request)
    if links is not None:
        _audit(actor, "video.room_links_viewed", room, request=request)
    return links


def rotate_link(room: VideoRoom, variant: str, actor, *, request=None) -> VideoRoom:
    """Wymienia klucz jednego linku. Stary link odpowiada od tej chwili 404 – pokój działa dalej."""
    if variant not in VARIANTS:
        raise _bad_request("Nieznany rodzaj linku.", "ROOM_LINK_VARIANT")
    if not room.is_open():
        raise DomainError("Pokój jest zamknięty albo wygasł – nie ma czego wymieniać.", "ROOM_CLOSED", 409)
    field = "host_key" if variant == HOST else "guest_key"
    setattr(room, field, new_key())
    room.save(update_fields=[field])
    _audit(actor, "video.room_link_rotated", room, {"link": variant}, request=request)
    return room


def close_room(room: VideoRoom, actor, *, request=None) -> VideoRoom:
    """Zamyka pokój: koniec przepustek z panelu i z bramki. Powtórne zamknięcie niczego nie zmienia."""
    if room.closed_at is not None:
        return room
    room.closed_at = timezone.now()
    room.save(update_fields=["closed_at"])
    _audit(actor, "video.room_closed", room, request=request)
    return room


def close_rooms_of(user, competition, actor, *, request=None) -> int:
    """Zamyka wszystkie otwarte pokoje, które ta osoba założyła jako członek komisji. Zwraca liczbę."""
    now = timezone.now()
    rooms = VideoRoom.objects.for_competition(competition).filter(
        created_by=user, created_as=RoomCreator.COMMITTEE, closed_at__isnull=True, expires_at__gt=now
    )
    count = 0
    for room in rooms:
        close_room(room, actor, request=request)
        count += 1
    return count


def panel_join_url(
    room: VideoRoom, user, *, moderator: bool, role: str, request=None, now=None, check_creator: bool = True
) -> str:
    """Adres pokoju z **krótką** przepustką dla osoby, która kliknęła „Dołącz” w panelu.

    Przepustka wygasa po ``JITSI_JWT_SESSION_MINUTES``, ale nigdy później niż sam pokój. ``role``
    idzie wyłącznie do audytu (``coordinator`` / ``committee`` / ``creator``). ``check_creator=False``
    wyłącznie dla koordynatora: wchodzi także do pokoju autora, który stracił uprawnienie, bo to on
    decyduje, czy taki pokój zamknąć – więc musi móc do niego zajrzeć.
    """
    now = now or timezone.now()
    allowed = panel_joinable(room, now) if check_creator else (jwt_enabled() and room.is_open(now))
    if not allowed:
        raise DomainError(
            "Ten pokój jest zamknięty albo stracił ważność.", "ROOM_CLOSED", status.HTTP_409_CONFLICT
        )
    token = issue(
        room.room_name,
        not_before=now,
        expires_at=min(now + session_lifetime(), room.expires_at),
        display_name=short_name(user),
        moderator=moderator,
    )
    _audit(user, "video.room_joined", room, {"role": role, "moderator": bool(moderator)}, request=request)
    return join_url(room.url, token)


def room_for_key(competition, key: str) -> tuple[VideoRoom, str] | None:
    """Pokój i wariant linku dla klucza z adresu bramki – wyłącznie w konkursie żądania."""
    key = (key or "").strip()
    if not key or len(key) > 64:
        return None
    room = (
        VideoRoom.objects.for_competition(competition)
        .filter(models.Q(host_key=key) | models.Q(guest_key=key))
        .select_related("competition")
        .first()
    )
    if room is None:
        return None
    # Porównanie stałoczasowe zamiast „który z dwóch pasował w SQL”: wynik i tak przyszedł z bazy,
    # ale reguła „klucz porównujemy jak sekret” ma być jedna dla całego modułu.
    variant = HOST if secrets.compare_digest(room.host_key, key) else GUEST
    return room, variant


def gateway_join_url(room: VideoRoom, variant: str, *, display_name: str, user=None, request=None) -> str:
    """Przepustka na kilka minut dla wejścia z linku-zaproszenia. Moderator wyłącznie z linku gospodarza."""
    now = timezone.now()
    if not jwt_enabled() or not room.is_open(now):
        raise DomainError(
            "Ten pokój jest zamknięty albo stracił ważność.", "ROOM_CLOSED", status.HTTP_410_GONE
        )
    token = issue(
        room.room_name,
        not_before=now,
        expires_at=min(now + gateway_lifetime(), room.expires_at),
        display_name=display_name,
        moderator=variant == HOST,
    )
    # Bez nazwy wyświetlanej w audycie: gość wpisuje ją sam i bywa nią imię i nazwisko.
    _audit(
        user if getattr(user, "is_authenticated", False) else None,
        "video.room_joined",
        room,
        {"role": f"{variant}_link", "moderator": variant == HOST},
        request=request,
    )
    return join_url(room.url, token)


def set_room_issuer(member, *, granted: bool, actor, request=None):
    """Nadaje albo odbiera członkowi komisji prawo zakładania pokoi. Audyt bez danych osobowych."""
    granted = bool(granted)
    if member.video_room_issuer == granted:
        return member
    member.video_room_issuer = granted
    member.save(update_fields=["video_room_issuer"])

    from apps.core.models import audit

    audit(actor, "video.issuer_granted" if granted else "video.issuer_revoked", member, {}, request=request)
    return member


# --- odczyt dla ekranów -------------------------------------------------------------------------


def rooms_for_committee(user, competition, now=None) -> list[VideoRoom]:
    """Pokoje udostępnione komisji, do których panel tej osoby dziś wpuszcza.

    Pusta lista **bez zapytania o pokoje**, gdy funkcja jest wyłączona – panel recenzenta
    w konkursie bez przepustek pyta bazę o dokładnie to samo, co przed v0.39.0.
    """
    if not jwt_enabled() or competition is None:
        return []
    if committee_member(user, competition) is None:
        return []
    now = now or timezone.now()
    rooms = (
        VideoRoom.objects.for_competition(competition)
        .filter(committee_access=True, closed_at__isnull=True, expires_at__gt=now)
        .select_related("created_by", "competition")
        .order_by("expires_at", "id")
    )
    return [room for room in rooms if creator_still_allowed(room)]


def committee_rooms_context(user, competition) -> dict:
    """Kontekst karty „Pokoje wideo komisji” w panelach komisji. Pusty słownik bez zapytań,
    gdy przepustki są wyłączone – panele mają wtedy co do zapytania ten sam budżet, co przed v0.39.0."""
    if not jwt_enabled():
        return {}
    from .interviews import slots_for_committee

    if committee_member(user, competition) is None:
        return {}
    return {
        "committee_rooms": rooms_for_committee(user, competition),
        "video_room_issuer": room_issuer(user, competition) is not None,
        # Rozmowy kwalifikacyjne prowadzi komisja (v0.39.0): terminy z zapisami, bez danych ponad
        # to, co widać w pokoju (``interviews.slots_for_committee``). Tylko przy przepustkach – bez
        # nich komisja dostaje adresy pokoi od koordynatora, jak przed tą wersją.
        "committee_interviews": slots_for_committee(competition) if competition is not None else [],
    }
