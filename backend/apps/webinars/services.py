"""Reguły webinarów: kto widzi, kto prowadzi, kiedy wolno wejść, i czynności na pokoju LiveKit.

Wszystkie bramki stoją **tutaj**, a nie w szablonie ani w JS pokoju (``docs/tasks/WEB-01.md`` § 4).
Widok tylko pyta i zamienia odpowiedź na stronę: brak prawa do webinaru = 404 (jak obiekt innego
konkursu – istnienie cudzego webinaru nie jest niczyją informacją), webinar poza oknem albo jeszcze
nierozpoczęty = komunikat dla człowieka, nigdy błąd.

**Jedna reguła odbiorców w dwóch postaciach.** :class:`Viewer` odpowiada na pytanie o jedną osobę
(lista w panelu, token, nagranie), :func:`audience_recipients` – o tysiące naraz (zaproszenie,
przypomnienie). Obie stoją obok siebie, bo rozjazd znaczyłby list do kogoś, kto po kliknięciu
dostaje 404 – albo panel z webinarem, o którym nikt nie dostał zaproszenia.

**Token LiveKit jest poświadczeniem.** Powstaje w :func:`join_token` / :func:`guest_token` przy
każdym wejściu, po sprawdzeniu roli i okna, żyje kilka minut i trafia wyłącznie do odpowiedzi
JSON ``no-store`` dla przeglądarki tej osoby – nie do szablonu, audytu ani logu. Osoba spoza grupy
odbiorców tokenu nie dostaje nigdy, więc nie ma czym otworzyć pokoju.

**Uprawnienia w pokoju nadaje serwer.** Widz ma ``canPublish=false`` w tokenie; „daj głos” po
podniesieniu ręki to polecenie serwerowe ``UpdateParticipant`` (:func:`set_speaker`), które może
wydać wyłącznie prowadzący – przez nasz widok, nie z przeglądarki.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.core.api import DomainError

from . import livekit
from .models import (
    FEATURE_FLAG,
    AttendeeRole,
    Audience,
    RecordingStatus,
    Webinar,
    WebinarAttendee,
    WebinarNotificationSettings,
    WebinarRecording,
    WebinarWebhookEvent,
    new_public_key,
)
from .storage import KEY_PREFIX, RECORDING_URL_TTL_SECONDS, get_recording_storage

logger = logging.getLogger(__name__)

ROLE_PRESENTER = "presenter"
ROLE_VIEWER = "viewer"

#: Ile minionych webinarów z nagraniami pokazuje panel odbiorcy.
PAST_LIMIT = 10
#: Jak długo po webinarze jego wiersz zostaje w części „minione” panelu odbiorcy (dni).
PAST_DAYS = 90
#: Najdłuższa nazwa w pokoju (gość wpisuje ją sam).
GUEST_NAME_MAX_LENGTH = 40
#: Zdarzenie webhooka starsze niż tyle sekund jest odrzucane (powtórka przechwyconego zdarzenia);
#: LiveKit ponawia dostarczenie w ciągu kilku minut, więc kwadrans nie gubi prawdziwych ponowień.
WEBHOOK_MAX_AGE_SECONDS = 900
#: Jak długo pamiętamy identyfikatory przetworzonych zdarzeń (dni) – dłużej niż ``MAX_AGE`` z zapasem.
WEBHOOK_EVENT_RETENTION_DAYS = 7
#: Adres transmisji RTMP(S): wyłącznie ``rtmp://`` albo ``rtmps://`` z hostem.
RTMP_URL = re.compile(r"^rtmps?://[A-Za-z0-9.-]+(:\d+)?/\S+$")
#: Domyślny serwer YouTube Live, gdy koordynator wkleja sam klucz strumienia.
YOUTUBE_RTMP = "rtmps://a.rtmps.youtube.com/live2/"
#: Stany egressu, w których nagranie jest już zamknięte (``livekit.EgressStatus``).
EGRESS_FINISHED = frozenset({"EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED"})
EGRESS_FAILED_STATES = frozenset({"EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED"})
#: Kody Twirp, którymi serwer odpowiada na zatrzymanie egressu, który już się zakończył.
EGRESS_GONE_CODES = frozenset({"not_found", "failed_precondition"})
#: Po ilu minutach od startu aktywne nagranie uzgadniamy z serwerem (``ListEgress``) w zadaniu beat –
#: zgubiony webhook ``egress_ended`` nie może zostawić wiersza „nagrywa” na zawsze.
RECONCILE_AFTER_MINUTES = 10
#: Ile sekund pusty pokój czeka na powrót prowadzącego, zanim LiveKit go zamknie (``CreateRoom``).
ROOM_EMPTY_TIMEOUT_SECONDS = 600
#: Identyfikator w pokoju: pseudonim konta albo gościa – nic poza tym alfabetem nie przechodzi.
IDENTITY = re.compile(r"^[ug]-[0-9a-f]{20}$")


# --- przełączniki --------------------------------------------------------------------------------


def enabled(competition) -> bool:
    """Flaga konkursu ``webinars``. Bez zapytania – ``has_feature`` czyta pole wiersza."""
    return competition is not None and competition.has_feature(FEATURE_FLAG)


def available(competition) -> bool:
    """Flaga **i** skonfigurowany serwer LiveKit – dopiero wtedy odbiorcy widzą cokolwiek."""
    return enabled(competition) and livekit.configured()


def _unavailable() -> DomainError:
    return DomainError(
        _("Serwer webinarów nie odpowiada. Spróbuj za chwilę."),
        "LIVEKIT_UNAVAILABLE",
        status.HTTP_502_BAD_GATEWAY,
    )


def _require_server() -> None:
    if not livekit.configured():
        raise DomainError(
            "Serwer LiveKit nie jest skonfigurowany – poproś operatora platformy.",
            "LIVEKIT_NOT_CONFIGURED",
            409,
        )


# --- czas ----------------------------------------------------------------------------------------


def competition_zone(competition):
    """Strefa konkursu (``Competition.time_zone``) z odwrotem na strefę instalacji."""
    name = getattr(competition, "time_zone", "") or settings.TIME_ZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo(settings.TIME_ZONE)


def join_window(webinar: Webinar):
    """Okno wejścia odbiorcy: ``LEAD`` minut przed początkiem do ``GRACE`` minut po planowanym końcu."""
    lead = timedelta(minutes=max(0, int(settings.WEBINAR_JOIN_LEAD_MINUTES)))
    grace = timedelta(minutes=max(0, int(settings.WEBINAR_JOIN_GRACE_MINUTES)))
    return webinar.starts_at - lead, webinar.ends_at + grace


def window_state(webinar: Webinar, now=None) -> str:
    """``upcoming`` / ``open`` / ``closed`` – zakończony przyciskiem albo odwołany jest ``closed``."""
    now = now or timezone.now()
    if webinar.cancelled_at is not None or webinar.ended_at is not None:
        return "closed"
    opens, closes = join_window(webinar)
    if now < opens:
        return "upcoming"
    return "open" if now < closes else "closed"


# --- kto jest kim ---------------------------------------------------------------------------------


def is_coordinator(user, competition) -> bool:
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    return has_role(user, competition, CompetitionRole.COORDINATOR)


def co_moderator_allowed(user, competition) -> bool:
    """Współprowadzący musi być **dziś** koordynatorem albo aktywnym członkiem komisji tego konkursu.

    Sprawdzane przy każdym wejściu, nie tylko przy zapisie: odebranie roli albo zawieszenie
    członka komisji ma od razu zabrać mu prawa prowadzącego, bez edycji każdego webinaru.
    """
    from apps.competitions.video_rooms import committee_member

    return is_coordinator(user, competition) or committee_member(user, competition) is not None


def co_moderator_choices(competition):
    """Konta, które wolno wskazać jako współprowadzących: koordynatorzy i aktywna komisja konkursu."""
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CommitteeMember, CommitteeStatus, CompetitionRole, User

    committee = CommitteeMember.objects.for_competition(competition).filter(status=CommitteeStatus.ACTIVE)
    return (
        User.objects.filter(is_active=True)
        .filter(
            Q(pk__in=User.objects.filter(_role_filter(competition, CompetitionRole.COORDINATOR)).values("pk"))
            | Q(pk__in=committee.values("user_id"))
        )
        .exclude_anonymised()
        .order_by("last_name", "first_name", "pk")
    )


@dataclass
class Viewer:
    """Fakty o jednej osobie w jednym konkursie, policzone **raz** dla całej listy webinarów.

    Panel odbiorcy pyta o kilkanaście webinarów naraz; bez tego każde pytanie „czy ta osoba jest
    w grupie” szło by do bazy osobno. Fakty są te same, co w :func:`audience_recipients`.
    """

    user: object
    competition: object
    coordinator: bool = False
    committee: bool = False
    participant: object = None
    edition_member: bool = False
    stage_ids: set[int] = field(default_factory=set)
    captain: bool = False

    @classmethod
    def of(cls, user, competition) -> Viewer:
        from apps.accounts.anonymised import anonymised_q
        from apps.accounts.models import CompetitionRole, Participant
        from apps.accounts.services import has_role, participant_for
        from apps.competitions.models import StageEntry, StageEntryStatus, TeamMember
        from apps.competitions.services import current_edition
        from apps.competitions.video_rooms import committee_member

        viewer = cls(user=user, competition=competition)
        if user is None or not getattr(user, "is_authenticated", False) or competition is None:
            return viewer
        viewer.coordinator = is_coordinator(user, competition)
        viewer.committee = committee_member(user, competition) is not None
        participant = None
        if has_role(user, competition, CompetitionRole.PARTICIPANT):
            participant = participant_for(user, competition)
        # Ta sama reguła, co ``audience_recipients`` (``exclude_anonymised``) – konto po anonimizacji
        # nie jest odbiorcą, nawet gdyby jakaś rola przetrwała w bazie.
        if (
            participant is not None
            and Participant.objects.filter(pk=participant.pk).filter(anonymised_q("user")).exists()
        ):
            participant = None
        viewer.participant = participant
        if participant is None:
            return viewer
        edition = current_edition(competition)
        viewer.stage_ids = set(
            StageEntry.objects.filter(participant=participant)
            .exclude(status=StageEntryStatus.DISQUALIFIED)
            .values_list("stage_id", flat=True)
        )
        if edition is not None:
            viewer.edition_member = (
                StageEntry.objects.filter(participant=participant, stage__edition=edition).exists()
                or user.date_joined >= edition.created_at
            )
            if competition.has_feature("team_entries"):
                viewer.captain = (
                    TeamMember.objects.for_competition(competition)
                    .filter(participant=participant, is_captain=True, team__edition=edition)
                    .exists()
                )
        return viewer

    def in_audience(self, webinar: Webinar) -> bool:
        """Czy osoba należy do grupy odbiorców (bez ról prowadzących – te osobno)."""
        if webinar.competition_id != getattr(self.competition, "pk", None):
            return False
        if webinar.include_committee and self.committee:
            return True
        audience = webinar.audience
        if audience == Audience.COMMITTEE:
            return self.committee
        if self.participant is None:
            return False
        if audience == Audience.COMPETITION:
            return True
        if audience == Audience.EDITION:
            return self.edition_member
        if audience == Audience.STAGE:
            return webinar.stage_id is not None and webinar.stage_id in self.stage_ids
        if audience == Audience.CAPTAINS:
            return self.captain
        return False

    def is_moderator(self, webinar: Webinar, co_moderator_ids: set[int] | None = None) -> bool:
        """Koordynator konkursu, autor albo współprowadzący, który **dziś** ma rolę w konkursie."""
        if webinar.competition_id != getattr(self.competition, "pk", None):
            return False
        if self.coordinator:
            return True
        # Poza koordynatorem prowadzić może wyłącznie ktoś z **aktywną** rolą komisji tego konkursu
        # (``co_moderator_allowed``) – sam wpis na liście współprowadzących po odebraniu roli nie wystarcza.
        if not self.committee:
            return False
        if webinar.created_by_id == self.user.pk:
            return True
        ids = (
            co_moderator_ids
            if co_moderator_ids is not None
            else set(webinar.co_moderators.values_list("pk", flat=True))
        )
        return self.user.pk in ids

    def role(self, webinar: Webinar) -> str | None:
        """``presenter`` / ``viewer`` / ``None`` (brak dostępu). Odwołany – tylko prowadzący."""
        if self.is_moderator(webinar):
            return ROLE_PRESENTER
        if webinar.cancelled_at is not None:
            return None
        return ROLE_VIEWER if self.in_audience(webinar) else None


def has_any_role(viewer: Viewer) -> bool:
    """Czy osoba ma w konkursie w ogóle rolę, dla której ekran webinarów ma sens."""
    return viewer.coordinator or viewer.committee or viewer.participant is not None


# --- odbiorcy hurtem ------------------------------------------------------------------------------


def audience_recipients(webinar: Webinar):
    """Konta odbiorców do listu: ta sama reguła, co :meth:`Viewer.in_audience`, zapytaniami zbiorczymi.

    Tylko adresy dostarczalne (``DELIVERABLE``), bez kont po anonimizacji i bez osób, które wyłączyły
    listy o webinarach. Prowadzący (koordynatorzy, współprowadzący) nie są tu odbiorcami – o swoim
    webinarze wiedzą z panelu.
    """
    from apps.accounts.messaging import DELIVERABLE, _role_filter, current_edition_participants
    from apps.accounts.models import CommitteeMember, CommitteeStatus, CompetitionRole, Participant, User
    from apps.competitions.models import StageEntry, StageEntryStatus, TeamMember
    from apps.competitions.services import current_edition

    competition = webinar.competition
    edition = current_edition(competition)
    participants = Participant.objects.for_competition(competition).exclude_anonymised()
    audience = webinar.audience
    if audience == Audience.COMPETITION:
        pass
    elif audience == Audience.EDITION:
        participants = current_edition_participants(participants, edition)
    elif audience == Audience.STAGE:
        stage = webinar.stage
        if stage is None or stage.edition.competition_id != competition.pk:
            participants = participants.none()
        else:
            entries = StageEntry.objects.filter(stage=stage, participant__isnull=False).exclude(
                status=StageEntryStatus.DISQUALIFIED
            )
            participants = participants.filter(pk__in=entries.values("participant_id"))
    elif audience == Audience.CAPTAINS and edition is not None and competition.has_feature("team_entries"):
        captains = TeamMember.objects.for_competition(competition).filter(
            is_captain=True, team__edition=edition
        )
        participants = participants.filter(pk__in=captains.values("participant_id"))
    else:
        participants = participants.none()

    selected = Q(
        pk__in=User.objects.filter(_role_filter(competition, CompetitionRole.PARTICIPANT))
        .filter(pk__in=participants.values("user_id"))
        .values("pk")
    )
    if audience == Audience.COMMITTEE or webinar.include_committee:
        active = CommitteeMember.objects.for_competition(competition).filter(status=CommitteeStatus.ACTIVE)
        reviewers = User.objects.filter(_role_filter(competition, CompetitionRole.REVIEWER)).filter(
            pk__in=active.values("user_id")
        )
        appeals = User.objects.filter(_role_filter(competition, CompetitionRole.APPEALS)).filter(
            pk__in=active.filter(is_appeals_committee=True).values("user_id")
        )
        selected |= Q(pk__in=reviewers.values("pk")) | Q(pk__in=appeals.values("pk"))
    return (
        User.objects.filter(DELIVERABLE)
        .filter(selected)
        .exclude_anonymised()
        .exclude(webinar_notification_settings__email_on_webinar=False)
        .select_related("preference")
        .order_by("pk")
        .distinct()
    )


# --- lista dla odbiorcy ---------------------------------------------------------------------------


def webinars_for(viewer: Viewer, now=None) -> tuple[list[Webinar], list[Webinar]]:
    """Nadchodzące (z trwającymi) i minione webinary tej osoby – po regule :meth:`Viewer.role`."""
    now = now or timezone.now()
    rows = list(
        Webinar.objects.for_competition(viewer.competition)
        .filter(cancelled_at__isnull=True, starts_at__gte=now - timedelta(days=PAST_DAYS))
        .select_related("competition", "stage")
        .prefetch_related("co_moderators")
        .order_by("starts_at", "id")
    )
    upcoming, past = [], []
    for webinar in rows:
        ids = {user.pk for user in webinar.co_moderators.all()}
        if not (viewer.is_moderator(webinar, ids) or viewer.in_audience(webinar)):
            continue
        (past if window_state(webinar, now) == "closed" else upcoming).append(webinar)
    past.reverse()
    return upcoming, past[:PAST_LIMIT]


def committee_webinars_context(user, competition) -> dict:
    """Karta „Webinary” w panelu recenzenta i komisji odwoławczej. Pusty słownik **bez zapytań**,
    gdy konkurs nie ma webinarów – panele komisji kosztują wtedy dokładnie tyle, co przed WEB-01."""
    if not available(competition):
        return {}
    viewer = Viewer.of(user, competition)
    upcoming, _past = webinars_for(viewer)
    return {"committee_webinars": upcoming[:5], "committee_webinars_on": True}


# --- pomocnicze ----------------------------------------------------------------------------------


def pseudonym(user) -> str:
    """``identity`` w LiveKit: stały pseudonim konta (HMAC), a nie ``pk`` ani e-mail.

    Identyfikator widzą wszyscy w pokoju (SDK go udostępnia) i stoi w webhookach; pozwala połączyć
    wejścia jednej osoby z jej wierszem obecności, ale poza platformą nic nie mówi.
    """
    key = f"webinars-livekit-user:{settings.SECRET_KEY}".encode()
    return "u-" + hmac.new(key, str(user.pk).encode(), hashlib.sha256).hexdigest()[:20]


def new_guest_identity() -> str:
    return "g-" + secrets.token_hex(10)


def display_name(user, fallback: str = "") -> str:
    """Nazwa w pokoju – „Imię N.”, jak w pokojach Jitsi (widzą ją wszyscy obecni)."""
    from apps.competitions.jitsi_jwt import short_name

    return short_name(user) or fallback or _("Uczestnik")


def clean_guest_name(raw: str) -> str:
    from apps.competitions.video_rooms import clean_display_name

    return clean_display_name(raw)[:GUEST_NAME_MAX_LENGTH].strip()


def room_digest(webinar: Webinar) -> str:
    """Krótki skrót nazwy pokoju do audytu – łączy wpis z pokojem, nie jest nazwą."""
    return hashlib.sha256(webinar.room_name.encode()).hexdigest()[:12]


def _audit(actor, action: str, webinar: Webinar, extra: dict | None = None, request=None) -> None:
    from apps.core.models import audit

    audit(actor, action, webinar, {"room": room_digest(webinar), **(extra or {})}, request=request)


# --- zakładanie i zmiany (koordynator) ------------------------------------------------------------

#: Pola formularza, które przepisujemy do modelu. Lista zamknięta: ``room_key``, ``public_key``
#: i znaczniki stanu nie przychodzą od człowieka nigdy.
EDITABLE_FIELDS = (
    "title",
    "description",
    "starts_at",
    "duration_minutes",
    "audience",
    "stage",
    "include_committee",
    "public_link",
    "record",
    "email_reminder",
)


def _validate(competition, data: dict, co_moderators) -> None:
    """Reguły, których nie wolno pominąć niezależnie od formularza (izolacja konkursu, role)."""
    from apps.competitions.models import Stage

    audience = data.get("audience")
    stage = data.get("stage")
    if audience == Audience.STAGE:
        if stage is None or not Stage.objects.for_competition(competition).filter(pk=stage.pk).exists():
            raise DomainError("Wybierz etap tego konkursu.", "WEBINAR_STAGE_REQUIRED", 400)
    else:
        data["stage"] = None
    if audience == Audience.CAPTAINS and not competition.has_feature("team_entries"):
        raise DomainError("Ten konkurs nie ma drużyn.", "WEBINAR_NO_TEAMS", 400)
    for user in co_moderators:
        if not co_moderator_allowed(user, competition):
            raise DomainError(
                "Współprowadzącym może być tylko koordynator albo aktywny członek komisji tego konkursu.",
                "WEBINAR_CO_MODERATOR",
                400,
            )


def create_webinar(competition, actor, data: dict, co_moderators=(), request=None) -> Webinar:
    _validate(competition, data, co_moderators)
    with transaction.atomic():
        webinar = Webinar(competition=competition, created_by=actor if getattr(actor, "pk", None) else None)
        for name in EDITABLE_FIELDS:
            if name in data:
                setattr(webinar, name, data[name])
        webinar.save()
        webinar.co_moderators.set(co_moderators)
        _audit(
            actor,
            "webinar.created",
            webinar,
            {"audience": webinar.audience, "record": webinar.record, "public_link": webinar.public_link},
            request=request,
        )
    return webinar


def update_webinar(webinar: Webinar, actor, data: dict, co_moderators=(), request=None) -> Webinar:
    _validate(webinar.competition, data, co_moderators)
    changed = []
    with transaction.atomic():
        for name in EDITABLE_FIELDS:
            if name in data and getattr(webinar, name) != data[name]:
                setattr(webinar, name, data[name])
                changed.append(name)
        # Zmiana terminu po wysłanym przypomnieniu: nowe przypomnienie ma pójść przed nowym terminem.
        if "starts_at" in changed:
            webinar.reminder_sent_at = None
        # Link gościa włączony ponownie dostaje **nowy** klucz: stary link (rozesłany kiedyś, może
        # dalej, niż chciał organizator) nie ożywa razem z przełącznikiem.
        if "public_link" in changed and webinar.public_link:
            webinar.public_key = new_public_key()
        webinar.save()
        before = set(webinar.co_moderators.values_list("pk", flat=True))
        after = {user.pk for user in co_moderators}
        if before != after:
            webinar.co_moderators.set(co_moderators)
            changed.append("co_moderators")
        if changed:
            _audit(actor, "webinar.updated", webinar, {"fields": changed}, request=request)
    return webinar


def rotate_guest_link(webinar: Webinar, actor, request=None) -> Webinar:
    """Nowy klucz linku gościa – stary przestaje działać od razu (także dla gościa usuniętego
    z pokoju, który wróciłby z nową sesją i nowym identyfikatorem)."""
    if not webinar.public_link:
        raise DomainError("Link dla gości jest wyłączony.", "WEBINAR_LINK_OFF", 409)
    webinar.public_key = new_public_key()
    webinar.save(update_fields=["public_key", "updated_at"])
    _audit(actor, "webinar.guest_link_rotated", webinar, request=request)
    return webinar


def _close_room_quietly(webinar: Webinar) -> None:
    """Zamyka pokój i zatrzymuje egressy; awaria serwera nie wstrzymuje decyzji na platformie –
    bez tokenu i tak nikt już nie wejdzie, a trwające połączenia LiveKit zamknie po pustym pokoju."""
    if not livekit.configured():
        return
    for egress_id in [
        *webinar.recordings.filter(status=RecordingStatus.ACTIVE).values_list("egress_id", flat=True),
        *([webinar.stream_egress_id] if webinar.stream_egress_id else []),
    ]:
        try:
            livekit.stop_egress(egress_id)
        except livekit.LiveKitUnavailable, livekit.LiveKitError:
            logger.warning("Webinar %s: nie udało się zatrzymać egressu.", webinar.pk)
    try:
        livekit.delete_room(webinar.room_name)
    except livekit.LiveKitUnavailable, livekit.LiveKitError:
        logger.warning("Webinar %s: LiveKit nie potwierdził zamknięcia pokoju.", webinar.pk)


def cancel_webinar(webinar: Webinar, actor, request=None) -> Webinar:
    """Odwołuje webinar: znika odbiorcom, link gościa przestaje działać, trwający pokój się zamyka."""
    if webinar.cancelled_at is not None:
        return webinar
    if webinar.started_at is not None and webinar.ended_at is None:
        _close_room_quietly(webinar)
    webinar.cancelled_at = timezone.now()
    webinar.stream_egress_id = ""
    webinar.save(update_fields=["cancelled_at", "stream_egress_id", "updated_at"])
    _audit(actor, "webinar.cancelled", webinar, request=request)
    return webinar


def announce(webinar: Webinar, actor, request=None) -> bool:
    """„Wyślij zaproszenie e-mailem” – **raz** na webinar. ``False``, gdy już wysłane.

    Zajęcie jest warunkowym ``UPDATE``: dwa kliknięcia naraz (albo dwóch koordynatorów) nie wyślą
    dwóch kompletów listów. Same listy składa worker po commicie (``tasks.send_webinar_invitation``).
    """
    if webinar.cancelled_at is not None:
        raise DomainError("Webinar jest odwołany.", "WEBINAR_CANCELLED", 409)
    now = timezone.now()
    claimed = Webinar.objects.filter(pk=webinar.pk, announced_at__isnull=True).update(announced_at=now)
    if not claimed:
        return False
    webinar.announced_at = now
    _audit(actor, "webinar.announced", webinar, request=request)

    from .tasks import send_webinar_invitation

    pk = webinar.pk
    transaction.on_commit(lambda: send_webinar_invitation.delay(pk))
    return True


# --- pokój ---------------------------------------------------------------------------------------


def start(webinar: Webinar, user, request=None, now=None) -> Webinar:
    """„Rozpocznij”: od tej chwili odbiorcy w oknie wejścia dostają tokeny.

    Pokój w LiveKit powstaje sam przy pierwszym wejściu – platforma nie musi go zakładać. „Rozpocznij”
    po „Zakończ” otwiera webinar na nowo (prowadzący wrócił do sali), o ile okno jeszcze trwa.
    """
    now = now or timezone.now()
    _require_server()
    if webinar.cancelled_at is not None:
        raise DomainError("Webinar jest odwołany.", "WEBINAR_CANCELLED", 409)
    if now >= join_window(webinar)[1]:
        raise DomainError("Czas tego webinaru minął.", "WEBINAR_OVER", 409)
    first = webinar.started_at is None
    if first or webinar.ended_at is not None:
        webinar.started_at = webinar.started_at or now
        webinar.ended_at = None
        webinar.save(update_fields=["started_at", "ended_at", "updated_at"])
        _audit(user, "webinar.started", webinar, request=request)
    return webinar


def end(webinar: Webinar, user, request=None) -> Webinar:
    """„Zakończ”: zamyka pokój w LiveKit (rozłącza wszystkich) i znacznik u nas – nikt już nie wchodzi."""
    _require_server()
    _close_room_quietly(webinar)
    webinar.ended_at = timezone.now()
    webinar.stream_egress_id = ""
    webinar.save(update_fields=["ended_at", "stream_egress_id", "updated_at"])
    _audit(user, "webinar.ended", webinar, request=request)
    return webinar


def _viewer_gate(webinar: Webinar, now) -> None:
    """Okno i „rozpoczęty” dla wejścia widza – komunikaty dla człowieka (przetłumaczone)."""
    state = window_state(webinar, now)
    if state == "upcoming":
        local = timezone.localtime(join_window(webinar)[0], competition_zone(webinar.competition))
        raise DomainError(
            _("Wejście otworzy się %(when)s. Wróć wtedy na tę stronę.")
            % {"when": local.strftime("%d.%m.%Y, %H:%M")},
            "WEBINAR_NOT_YET",
            409,
        )
    if state == "closed":
        raise DomainError(_("Ten webinar już się zakończył."), "WEBINAR_OVER", 409)
    if webinar.started_at is None:
        raise DomainError(
            _("Prowadzący jeszcze nie rozpoczął webinaru. Spróbuj ponownie za kilka minut."),
            "WEBINAR_NOT_RUNNING",
            409,
        )


def _ensure_room(webinar: Webinar) -> None:
    """``CreateRoom`` przed wystawieniem tokenu (serwer ma ``auto_create: false``). Idempotentne –
    istniejący pokój zostaje bez zmian. Wołane wyłącznie po sprawdzeniu reguł wejścia, więc pokój
    po „Zakończ” nie powstaje na nowo z tokenu wystawionego wcześniej."""
    try:
        livekit.create_room(webinar.room_name, empty_timeout=ROOM_EMPTY_TIMEOUT_SECONDS)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError as exc:
        raise DomainError(f"LiveKit odmówił założenia pokoju ({exc.code}).", "LIVEKIT_ROOM", 502) from exc


def _refuse_removed(webinar: Webinar, identity: str) -> None:
    """Osoba usunięta z pokoju przez prowadzącego nie dostaje nowego tokenu (M1 przeglądu)."""
    if WebinarAttendee.objects.filter(webinar=webinar, identity=identity, removed_at__isnull=False).exists():
        raise DomainError(
            _("Prowadzący usunął Cię z tego webinaru."), "WEBINAR_REMOVED", status.HTTP_403_FORBIDDEN
        )


def _remember_attendee(webinar: Webinar, *, identity: str, user, name: str, role: str) -> None:
    """Wiersz obecności przy pierwszym tokenie (kto to jest); czasy dopiszą webhooki."""
    try:
        with transaction.atomic():
            WebinarAttendee.objects.get_or_create(
                webinar=webinar,
                identity=identity,
                defaults={"user": user, "name": name[:80], "role": role},
            )
    except IntegrityError:  # dwa tokeny naraz – wiersz już jest
        pass


def join_token(webinar: Webinar, viewer: Viewer, request=None, now=None) -> dict:
    """Token wejścia z panelu. Prowadzący – w każdej chwili do końca okna (i rozpoczyna webinar);
    odbiorca – w oknie i po „Rozpocznij”. Brak prawa do webinaru – ``DomainError`` 404."""
    now = now or timezone.now()
    role = viewer.role(webinar)
    if role is None:
        raise DomainError("Nie ma takiego webinaru.", "WEBINAR_NOT_FOUND", 404)
    if not livekit.configured():
        raise _unavailable()
    presenter = role == ROLE_PRESENTER
    if presenter:
        start(webinar, viewer.user, request=request, now=now)
    else:
        _viewer_gate(webinar, now)
    identity = pseudonym(viewer.user)
    if not presenter:
        _refuse_removed(webinar, identity)
    _ensure_room(webinar)
    name = display_name(viewer.user, "Prowadzący" if presenter else "")
    _remember_attendee(
        webinar,
        identity=identity,
        user=viewer.user,
        name=name,
        role=AttendeeRole.PRESENTER if presenter else AttendeeRole.VIEWER,
    )
    _audit(viewer.user, "webinar.joined", webinar, {"role": role}, request=request)
    token = livekit.access_token(identity=identity, name=name, room=webinar.room_name, presenter=presenter)
    return {"url": livekit.ws_url(), "token": token, "identity": identity, "role": role}


def webinar_for_key(competition, key: str) -> Webinar | None:
    """Webinar z włączonym linkiem dla gości o tym kluczu – wyłącznie w konkursie żądania."""
    key = (key or "").strip()
    if not key or len(key) > 64:
        return None
    webinar = (
        Webinar.objects.for_competition(competition)
        .filter(public_key=key, public_link=True, cancelled_at__isnull=True)
        .select_related("competition")
        .first()
    )
    if webinar is None or not secrets.compare_digest(webinar.public_key, key):
        return None
    return webinar


def guest_token(webinar: Webinar, *, identity: str, name: str, user=None, request=None, now=None) -> dict:
    """Gość z linku: widz bez konta, z identyfikatorem z sesji. Link wyłączony – 410."""
    now = now or timezone.now()
    if not webinar.public_link or webinar.cancelled_at is not None:
        raise DomainError(_("Ten link nie jest już aktywny."), "WEBINAR_LINK_OFF", 410)
    if not livekit.configured():
        raise _unavailable()
    if not IDENTITY.match(identity or ""):
        raise DomainError("Nieprawidłowy identyfikator gościa.", "WEBINAR_GUEST", 400)
    _viewer_gate(webinar, now)
    _refuse_removed(webinar, identity)
    _ensure_room(webinar)
    # Gość jest oznaczony w samej nazwie (widzą ją wszyscy w pokoju): nikt spoza konkursu nie może
    # podpisać się tak, żeby wyglądać jak uczestnik albo prowadzący z konta.
    name = _("%(name)s (gość)") % {"name": name}
    _remember_attendee(webinar, identity=identity, user=None, name=name, role=AttendeeRole.GUEST)
    # Bez nazwy gościa w audycie – wpisuje ją sam i bywa nią imię i nazwisko.
    _audit(user, "webinar.joined", webinar, {"role": "guest"}, request=request)
    token = livekit.access_token(identity=identity, name=name, room=webinar.room_name, presenter=False)
    return {"url": livekit.ws_url(), "token": token, "identity": identity, "role": ROLE_VIEWER}


def _attendee(webinar: Webinar, identity: str) -> WebinarAttendee:
    if not IDENTITY.match(identity or ""):
        raise DomainError("Nie ma takiej osoby w pokoju.", "WEBINAR_ATTENDEE", 404)
    found = WebinarAttendee.objects.filter(webinar=webinar, identity=identity).first()
    if found is None:
        raise DomainError("Nie ma takiej osoby w pokoju.", "WEBINAR_ATTENDEE", 404)
    return found


def _still_allowed(webinar: Webinar, attendee: WebinarAttendee) -> bool:
    """Czy osoba z wiersza obecności **dziś** ma prawo być w pokoju (L3 przeglądu): konto – rola
    w webinarze (odbiorca albo prowadzący), gość – włączony link; usunięty – nie."""
    if attendee.removed_at is not None:
        return False
    if attendee.user_id is not None:
        return Viewer.of(attendee.user, webinar.competition).role(webinar) is not None
    return webinar.public_link and webinar.cancelled_at is None


def set_speaker(webinar: Webinar, actor, identity: str, speaker: bool, request=None) -> None:
    """„Daj głos” / „odbierz głos” widzowi (np. po podniesieniu ręki) – ``UpdateParticipant``.

    Prawo prowadzącego sprawdza widok (``Viewer.is_moderator``). Identyfikator musi należeć do
    osoby, która dostała token do **tego** webinaru – podrobiony nie dotknie innego pokoju.
    """
    _require_server()
    attendee = _attendee(webinar, identity)
    if speaker and not _still_allowed(webinar, attendee):
        raise DomainError(
            "Ta osoba nie należy już do odbiorców webinaru – nie można dać jej głosu.",
            "WEBINAR_NOT_AUDIENCE",
            409,
        )
    try:
        livekit.set_can_publish(webinar.room_name, attendee.identity, speaker)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError as exc:
        raise DomainError("Tej osoby nie ma teraz w pokoju.", "WEBINAR_NOT_IN_ROOM", 409) from exc
    _audit(
        actor, "webinar.speaker_granted" if speaker else "webinar.speaker_revoked", webinar, request=request
    )


def remove_from_room(webinar: Webinar, actor, identity: str, request=None) -> None:
    """Wyprasza osobę z pokoju **i** zapamiętuje to (``removed_at``) – bez znacznika wróciłaby od razu
    z nowym tokenem. Prowadzącego nie da się usunąć (prawa nadaje mu rola, nie ten wiersz)."""
    _require_server()
    attendee = _attendee(webinar, identity)
    if attendee.role == AttendeeRole.PRESENTER:
        raise DomainError("Prowadzącego nie można usunąć z pokoju.", "WEBINAR_PRESENTER", 409)
    WebinarAttendee.objects.filter(pk=attendee.pk).update(removed_at=timezone.now())
    try:
        livekit.remove_participant(webinar.room_name, attendee.identity)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError:
        # Już wyszedł – znacznik i tak zostaje, więc nie wróci.
        logger.info("Webinar %s: osoby nie było w pokoju przy usuwaniu.", webinar.pk)
    _audit(
        actor, "webinar.participant_removed", webinar, {"guest": attendee.user_id is None}, request=request
    )


def readmit(webinar: Webinar, actor, identity: str, request=None) -> None:
    """„Wpuść ponownie” – zdejmuje znacznik usunięcia."""
    attendee = _attendee(webinar, identity)
    WebinarAttendee.objects.filter(pk=attendee.pk).update(removed_at=None)
    _audit(actor, "webinar.participant_readmitted", webinar, request=request)


def present_count(webinar: Webinar) -> int:
    """Liczba osób w pokoju według webhooków (wejście bez późniejszego wyjścia)."""
    return sum(1 for attendee in webinar.attendees.all() if attendee.present)


# --- nagrania i transmisja -------------------------------------------------------------------------


def recording_key(webinar: Webinar, now) -> str:
    return f"{KEY_PREFIX}{webinar.competition.slug}/{webinar.room_key}/{now:%Y%m%d-%H%M%S}.mp4"


def start_recording(webinar: Webinar, actor, request=None) -> WebinarRecording:
    """Egress „room composite” do MP4 w prywatnym buckecie. Jedno aktywne nagranie naraz."""
    _require_server()
    if not webinar.record:
        raise DomainError(
            "Nagrywanie tego webinaru jest wyłączone w jego ustawieniach.", "WEBINAR_NO_RECORD", 409
        )
    if webinar.started_at is None or webinar.ended_at is not None or webinar.cancelled_at is not None:
        raise DomainError("Nagrywać można tylko trwający webinar.", "WEBINAR_NOT_LIVE", 409)
    if webinar.recordings.filter(status=RecordingStatus.ACTIVE).exists():
        raise DomainError("Nagrywanie już trwa.", "WEBINAR_RECORDING_ACTIVE", 409)
    now = timezone.now()
    key = recording_key(webinar, now)
    try:
        egress_id = livekit.start_room_recording(webinar.room_name, key)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError as exc:
        raise DomainError(f"LiveKit odmówił nagrywania ({exc.code}).", "LIVEKIT_EGRESS", 502) from exc
    if not egress_id:
        raise DomainError("LiveKit nie zwrócił identyfikatora nagrania.", "LIVEKIT_EGRESS", 502)
    recording = WebinarRecording.objects.create(
        webinar=webinar, egress_id=egress_id, storage_key=key, started_by=actor, started_at=now
    )
    _audit(actor, "webinar.recording_started", webinar, request=request)
    return recording


def stop_recording(webinar: Webinar, actor, request=None) -> int:
    """Zatrzymuje aktywne nagrania. Stan „gotowe” i rozmiar dopisze webhook ``egress_ended``."""
    _require_server()
    stopped = 0
    for recording in webinar.recordings.filter(status=RecordingStatus.ACTIVE):
        try:
            livekit.stop_egress(recording.egress_id)
        except livekit.LiveKitUnavailable as exc:
            raise _unavailable() from exc
        except livekit.LiveKitError as exc:
            # Egress już się skończył (zgubiony webhook) – stan bierzemy od serwera, zamiast czekać
            # na zdarzenie, które nie przyjdzie.
            logger.info(
                "Webinar %s: egress %s już zakończony (%s).", webinar.pk, recording.egress_id, exc.code
            )
            if exc.code in EGRESS_GONE_CODES:
                reconcile_recording(recording)
        stopped += 1
    if stopped:
        _audit(actor, "webinar.recording_stopped", webinar, request=request)
    return stopped


def reconcile_recording(recording: WebinarRecording, now=None) -> str:
    """Uzgadnia aktywne nagranie z serwerem (``ListEgress``). Zwraca stan po uzgodnieniu.

    Egress zakończony – ten sam zapis, co z webhooka ``egress_ended``. Egress, którego serwer nie
    zna – nagranie nieudane (plik nie powstał albo serwer stracił stan). Awaria sieci – bez zmian
    (następny przebieg spróbuje znowu).
    """
    if recording.status != RecordingStatus.ACTIVE:
        return recording.status
    try:
        items = livekit.list_egress(recording.egress_id)
    except livekit.LiveKitUnavailable:
        return recording.status
    except livekit.LiveKitError as exc:
        if exc.code != "not_found":
            return recording.status
        items = []
    now = now or timezone.now()
    item = next(
        (
            row
            for row in items
            if str(row.get("egress_id") or row.get("egressId") or "") == recording.egress_id
        ),
        None,
    )
    if item is None:
        WebinarRecording.objects.filter(pk=recording.pk, status=RecordingStatus.ACTIVE).update(
            status=RecordingStatus.FAILED, error="serwer LiveKit nie zna tego nagrania", ended_at=now
        )
    elif str(item.get("status") or "") in EGRESS_FINISHED:
        _handle_egress(item, "egress_ended", now)
    recording.refresh_from_db()
    return recording.status


def reconcile_stale_recordings(now=None) -> int:
    """Zadanie beat: aktywne nagrania starsze niż :data:`RECONCILE_AFTER_MINUTES` – z serwerem."""
    if not livekit.configured():
        return 0
    now = now or timezone.now()
    stale = WebinarRecording.objects.filter(
        status=RecordingStatus.ACTIVE, started_at__lt=now - timedelta(minutes=RECONCILE_AFTER_MINUTES)
    )
    return sum(1 for recording in stale if reconcile_recording(recording, now) != RecordingStatus.ACTIVE)


def mark_recording_failed(webinar: Webinar, pk, actor, request=None) -> WebinarRecording:
    """Koordynator zamyka wiszące nagranie ręcznie: najpierw próba zatrzymania i uzgodnienia,
    a gdy serwer dalej nic nie mówi – stan „błąd” z adnotacją."""
    recording = own_recording(webinar, pk)
    if recording.status != RecordingStatus.ACTIVE:
        return recording
    if livekit.configured():
        try:
            livekit.stop_egress(recording.egress_id)
        except livekit.LiveKitUnavailable, livekit.LiveKitError:
            pass
        if reconcile_recording(recording) != RecordingStatus.ACTIVE:
            _audit(
                actor, "webinar.recording_reconciled", webinar, {"recording": recording.pk}, request=request
            )
            return recording
    WebinarRecording.objects.filter(pk=recording.pk).update(
        status=RecordingStatus.FAILED, error="oznaczone ręcznie jako nieudane", ended_at=timezone.now()
    )
    recording.refresh_from_db()
    _audit(actor, "webinar.recording_marked_failed", webinar, {"recording": recording.pk}, request=request)
    return recording


def rtmp_target(raw: str) -> str:
    """Adres RTMP(S) z pola formularza: pełny adres albo sam klucz YouTube (wtedy serwer YouTube)."""
    raw = (raw or "").strip()
    if not raw or any(char.isspace() for char in raw) or len(raw) > 500:
        raise DomainError("Podaj adres RTMP albo klucz transmisji.", "WEBINAR_STREAM_URL", 400)
    if "://" not in raw:
        raw = YOUTUBE_RTMP + raw
    if not RTMP_URL.match(raw):
        raise DomainError(
            "Adres transmisji musi zaczynać się od rtmp:// albo rtmps://.", "WEBINAR_STREAM_URL", 400
        )
    return raw


def start_stream(webinar: Webinar, actor, raw_target: str, request=None) -> None:
    """Transmisja RTMP (np. YouTube Live). **Klucza strumienia nie zapisujemy** – idzie wyłącznie
    w żądaniu do LiveKit: klucz przechowany u nas byłby kolejnym sekretem do szyfrowania i rotacji,
    a koordynator i tak ma go w panelu YouTube. W audycie stoi tylko host docelowy."""
    _require_server()
    if webinar.started_at is None or webinar.ended_at is not None or webinar.cancelled_at is not None:
        raise DomainError("Transmitować można tylko trwający webinar.", "WEBINAR_NOT_LIVE", 409)
    if webinar.stream_egress_id:
        raise DomainError("Transmisja już trwa.", "WEBINAR_STREAM_ACTIVE", 409)
    target = rtmp_target(raw_target)
    try:
        egress_id = livekit.start_room_stream(webinar.room_name, target)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError as exc:
        raise DomainError(f"LiveKit odmówił transmisji ({exc.code}).", "LIVEKIT_EGRESS", 502) from exc
    webinar.stream_egress_id = egress_id
    webinar.save(update_fields=["stream_egress_id", "updated_at"])
    host = target.split("://", 1)[1].split("/", 1)[0]
    _audit(actor, "webinar.stream_started", webinar, {"host": host}, request=request)


def stop_stream(webinar: Webinar, actor, request=None) -> None:
    _require_server()
    if not webinar.stream_egress_id:
        return
    try:
        livekit.stop_egress(webinar.stream_egress_id)
    except livekit.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit.LiveKitError:
        pass
    webinar.stream_egress_id = ""
    webinar.save(update_fields=["stream_egress_id", "updated_at"])
    _audit(actor, "webinar.stream_stopped", webinar, request=request)


def own_recording(webinar: Webinar, pk) -> WebinarRecording:
    """Nagranie **tego** webinaru albo 404 – podrobiony identyfikator nie dotknie cudzego nagrania."""
    found = WebinarRecording.objects.filter(webinar=webinar, pk=pk).first() if str(pk).isdigit() else None
    if found is None:
        raise DomainError("Nie ma takiego nagrania.", "WEBINAR_RECORDING_NOT_FOUND", 404)
    return found


def set_recording_published(webinar: Webinar, pk, publish: bool, actor, request=None) -> WebinarRecording:
    recording = own_recording(webinar, pk)
    if publish and recording.status != RecordingStatus.COMPLETE:
        raise DomainError("Opublikować można tylko gotowe nagranie.", "WEBINAR_RECORDING_NOT_READY", 409)
    recording.published = bool(publish)
    recording.published_at = timezone.now() if publish else None
    recording.save(update_fields=["published", "published_at"])
    action = "webinar.recording_published" if publish else "webinar.recording_unpublished"
    _audit(actor, action, webinar, {"recording": recording.pk}, request=request)
    return recording


def delete_recording(webinar: Webinar, pk, actor, request=None) -> None:
    """Kasuje plik z bucketu i wiersz. Nagrywające jeszcze nagranie – najpierw „Zatrzymaj”."""
    recording = own_recording(webinar, pk)
    if recording.status == RecordingStatus.ACTIVE:
        raise DomainError("Najpierw zatrzymaj nagrywanie.", "WEBINAR_RECORDING_ACTIVE", 409)
    if recording.storage_key.startswith(KEY_PREFIX):
        get_recording_storage().delete(recording.storage_key)
    recording_pk = recording.pk
    recording.delete()
    _audit(actor, "webinar.recording_deleted", webinar, {"recording": recording_pk}, request=request)


def published_recordings(webinar: Webinar) -> list[WebinarRecording]:
    return list(webinar.recordings.filter(published=True, status=RecordingStatus.COMPLETE))


def recording_url_for(webinar: Webinar, viewer: Viewer, pk) -> str:
    """Krótko żyjący adres odtwarzania – po sprawdzeniu dostępu do webinaru i publikacji nagrania."""
    if viewer.role(webinar) is None:
        raise DomainError("Nie ma takiego nagrania.", "WEBINAR_RECORDING_NOT_FOUND", 404)
    recording = own_recording(webinar, pk)
    allowed = recording.status == RecordingStatus.COMPLETE and (
        recording.published or viewer.is_moderator(webinar)
    )
    if not allowed or not recording.storage_key.startswith(KEY_PREFIX):
        raise DomainError("Nie ma takiego nagrania.", "WEBINAR_RECORDING_NOT_FOUND", 404)
    return get_recording_storage().presigned_get(
        recording.storage_key,
        ttl=RECORDING_URL_TTL_SECONDS,
        content_type="video/mp4",
        content_disposition="inline",
    )


# --- webhooki LiveKit -----------------------------------------------------------------------------


def _event_time(event: dict) -> datetime:
    """``createdAt`` zdarzenia (sekundy od epoki) albo teraz, gdy go brak."""
    try:
        return datetime.fromtimestamp(int(event.get("createdAt")), tz=UTC)
    except TypeError, ValueError, OverflowError, OSError:
        return timezone.now()


def _webinar_for_room(name: str) -> Webinar | None:
    """Webinar po nazwie pokoju – webhook jest wspólny dla instalacji, więc rozstrzyga sama nazwa."""
    name = (name or "").strip()
    key = name.rsplit("-", 1)[-1] if name.startswith("olimp-") else ""
    if not key:
        return None
    webinar = Webinar._base_manager.select_related("competition").filter(room_key=key).first()
    return webinar if webinar is not None and webinar.room_name == name else None


def _recording_key_from(egress: dict, recording: WebinarRecording) -> str:
    """Klucz pliku z ``fileResults`` – wyłącznie w prefiksie tego webinaru; inaczej zostaje nasz."""
    prefix = recording.storage_key.rsplit("/", 1)[0] + "/"
    for item in egress.get("fileResults") or egress.get("file_results") or []:
        filename = str(item.get("filename") or "")
        if filename.startswith(prefix):
            return filename
    return recording.storage_key


def _handle_egress(egress: dict, event_name: str, at) -> None:
    egress_id = str(egress.get("egressId") or egress.get("egress_id") or "")
    if not egress_id:
        return
    recording = WebinarRecording.objects.filter(egress_id=egress_id).first()
    if recording is None:
        # Koniec transmisji RTMP – zdejmujemy znacznik z webinaru.
        if event_name == "egress_ended":
            Webinar._base_manager.filter(stream_egress_id=egress_id).update(stream_egress_id="")
        return
    if event_name != "egress_ended":
        return
    status_value = str(egress.get("status") or "")
    failed = status_value in EGRESS_FAILED_STATES or bool(egress.get("error"))
    results = egress.get("fileResults") or egress.get("file_results") or [{}]
    first = results[0] if results else {}
    recording.status = RecordingStatus.FAILED if failed else RecordingStatus.COMPLETE
    recording.error = str(egress.get("error") or "")[:200]
    recording.storage_key = _recording_key_from(egress, recording)
    try:
        recording.size = int(first.get("size")) if first.get("size") is not None else None
    except TypeError, ValueError:
        recording.size = None
    try:
        # ``duration`` w nanosekundach (int64 w JSON-ie jako napis).
        recording.duration_seconds = (
            int(int(first.get("duration")) / 1_000_000_000) if first.get("duration") else None
        )
    except TypeError, ValueError:
        recording.duration_seconds = None
    recording.ended_at = at
    recording.save()


def handle_webhook(event: dict) -> str:
    """Przetwarza **zweryfikowane** zdarzenie. Zwraca ``ok`` / ``duplicate`` / ``stale`` / ``ignored``.

    Powtórka: zdarzenie o identyfikatorze już widzianym nie zmienia niczego; zdarzenie starsze niż
    :data:`WEBHOOK_MAX_AGE_SECONDS` jest odrzucane – przechwycone i podpisane zdarzenie nie cofnie
    stanu pokoju po godzinie.
    """
    name = str(event.get("event") or "")
    event_id = str(event.get("id") or "")[:64]
    at = _event_time(event)
    if abs((timezone.now() - at).total_seconds()) > WEBHOOK_MAX_AGE_SECONDS:
        return "stale"
    # Zapis „to zdarzenie już było” i jego skutki w **jednej** transakcji: błąd w przetwarzaniu
    # cofa także znacznik, więc ponowienie LiveKit nie zostanie odrzucone jako powtórka (L1 przeglądu).
    try:
        with transaction.atomic():
            if event_id:
                try:
                    with transaction.atomic():
                        WebinarWebhookEvent.objects.create(event_id=event_id, event=name[:40])
                except IntegrityError:
                    return "duplicate"
            return _apply_event(name, event, at)
    except IntegrityError:  # pragma: no cover - wyścig dwóch dostarczeń tego samego zdarzenia
        return "duplicate"


def _apply_event(name: str, event: dict, at) -> str:
    """Skutki jednego zdarzenia (wewnątrz transakcji :func:`handle_webhook`)."""
    if name.startswith("egress_"):
        _handle_egress(event.get("egressInfo") or event.get("egress_info") or {}, name, at)
        return "ok"
    room = event.get("room") or {}
    webinar = _webinar_for_room(str(room.get("name") or ""))
    if webinar is None:
        return "ignored"
    if name == "room_started":
        Webinar._base_manager.filter(pk=webinar.pk).update(live_started_at=at, live_finished_at=None)
    elif name == "room_finished":
        Webinar._base_manager.filter(pk=webinar.pk).update(live_finished_at=at)
        for attendee in webinar.attendees.filter(last_joined_at__isnull=False):
            if attendee.present:
                _leave(attendee, at)
    elif name in ("participant_joined", "participant_left"):
        participant = event.get("participant") or {}
        identity = str(participant.get("identity") or "")[:64]
        if not IDENTITY.match(identity):
            return "ignored"
        attendee, _created = WebinarAttendee.objects.get_or_create(
            webinar=webinar,
            identity=identity,
            defaults={"name": str(participant.get("name") or "")[:80], "role": AttendeeRole.GUEST},
        )
        if name == "participant_joined":
            WebinarAttendee.objects.filter(pk=attendee.pk).update(
                first_joined_at=attendee.first_joined_at or at, last_joined_at=at, left_at=None
            )
        elif attendee.present:
            _leave(attendee, at)
    else:
        return "ignored"
    return "ok"


def _leave(attendee: WebinarAttendee, at) -> None:
    seconds = max(0, int((at - attendee.last_joined_at).total_seconds()))
    WebinarAttendee.objects.filter(pk=attendee.pk).update(left_at=at, seconds=F("seconds") + seconds)


def purge_webhook_events(now=None) -> int:
    cutoff = (now or timezone.now()) - timedelta(days=WEBHOOK_EVENT_RETENTION_DAYS)
    deleted, _rows = WebinarWebhookEvent.objects.filter(received_at__lt=cutoff).delete()
    return deleted


# --- retencja i dane osobowe -----------------------------------------------------------------------


def purge_expired(now=None) -> dict[str, int]:
    """Retencja (M6 przeglądu): po ``WEBINAR_RETENTION_DAYS`` od końca webinaru znikają nagrania
    (plik i wiersz) i lista obecności. Sam webinar (tytuł, termin, odbiorcy) zostaje – to dokumentacja
    zajęć organizatora, bez danych uczestników. Wołane z zadania beat ``remind_webinars``."""
    now = now or timezone.now()
    days = int(getattr(settings, "WEBINAR_RETENTION_DAYS", 365) or 0)
    if days <= 0:
        return {"recordings": 0, "attendees": 0}
    cutoff = now - timedelta(days=days)
    # ``starts_at`` < granica − 8 h (najdłuższy webinar) to tanie, indeksowane zawężenie; dokładny
    # koniec (``ends_at``) liczymy już w Pythonie na tej krótkiej liście.
    expired = [
        webinar
        for webinar in Webinar._base_manager.filter(starts_at__lt=cutoff).only(
            "pk", "starts_at", "duration_minutes"
        )
        if webinar.ends_at < cutoff
    ]
    ids = [webinar.pk for webinar in expired]
    storage = get_recording_storage()
    recordings = 0
    for recording in WebinarRecording.objects.filter(webinar_id__in=ids).exclude(
        status=RecordingStatus.ACTIVE
    ):
        if recording.storage_key.startswith(KEY_PREFIX):
            try:
                storage.delete(recording.storage_key)
            except Exception:  # noqa: BLE001 - wiersz zostaje, następny przebieg spróbuje znowu
                logger.warning(
                    "Webinar %s: nie udało się skasować nagrania po retencji.", recording.webinar_id
                )
                continue
        recording.delete()
        recordings += 1
    attendees, _rows = WebinarAttendee.objects.filter(webinar_id__in=ids).delete()
    return {"recordings": recordings, "attendees": attendees}


def erase_for_user(user) -> int:
    """Anonimizacja konta: znikają wiersze obecności tej osoby, ustawienie listów i współprowadzenie.

    Lista obecności to dane o osobie (kiedy i jak długo była w pokoju), a nie dokumentacja zawodów –
    po anonimizacji nie ma do kogo jej przypisać, więc wiersze są kasowane, a nie odpinane od konta.
    """
    removed = WebinarAttendee.objects.filter(user=user).delete()[0]
    removed += WebinarNotificationSettings.objects.filter(user=user).delete()[0]
    for webinar in Webinar._base_manager.filter(co_moderators=user):
        webinar.co_moderators.remove(user)
        removed += 1
    return removed


def export_for_user(user) -> dict:
    """Sekcja eksportu danych konta: obecność na webinarach i ustawienie listów."""
    rows = (
        WebinarAttendee.objects.filter(user=user)
        .select_related("webinar__competition")
        .order_by("first_token_at")
    )
    return {
        "listy_o_webinarach": notifications_enabled(user),
        "obecnosc": [
            {
                "konkurs": row.webinar.competition.name,
                "webinar": row.webinar.title,
                "termin": row.webinar.starts_at.isoformat(),
                "rola": row.get_role_display(),
                "nazwa_w_pokoju": row.name,
                "pierwsze_wejscie": row.first_joined_at.isoformat() if row.first_joined_at else None,
                "czas_obecnosci_s": row.seconds,
                "usuniety_z_pokoju": row.removed_at.isoformat() if row.removed_at else None,
            }
            for row in rows
        ],
    }


# --- ustawienia listów ----------------------------------------------------------------------------


def notifications_enabled(user) -> bool:
    row = WebinarNotificationSettings.objects.filter(user=user).first()
    return True if row is None else row.email_on_webinar


def save_notifications(user, enabled_: bool) -> None:
    WebinarNotificationSettings.objects.update_or_create(
        user=user, defaults={"email_on_webinar": bool(enabled_)}
    )
