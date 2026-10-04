"""Reguły nadzoru zdalnego: kto nadzoruje kogo, kiedy uczeń jest „gotowy”, tokeny i czynności.

Wszystkie bramki stoją **tutaj** (``docs/tasks/PROC-01.md`` § 3, § 5), a nie w szablonach ani w JS:
widok pyta i zamienia odpowiedź na stronę albo JSON. Brak prawa do etapu albo ucznia – 404 (istnienie
cudzego ucznia nie jest niczyją informacją); zła chwila – zdanie dla człowieka.

**Zakres nadzorującego** (:class:`ProctorScope`) liczymy przy **każdym** żądaniu: odebranie roli
koordynatora, zawieszenie członka komisji albo odwołanie opiekuna z delegacji zabiera nadzór od razu,
bez edycji przydziałów. Opiekun drużyny jest zamknięty w grupie swojej delegacji dwa razy: lista
z serwera (queryset) i **token LiveKit tylko do pokoju tej grupy** – serwer LiveKit nie wpuści go do
pokoju innej delegacji, więc nie zasubskrybuje ucznia innego kraju nawet przerobionym skryptem.

**Token jest poświadczeniem** – jak w WEB-01: powstaje przy każdym wejściu, trafia wyłącznie do
odpowiedzi JSON ``no-store``, nie do szablonu, audytu ani logu.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.api import DomainError

from . import delegations, livekit_api, windows
from .models import (
    FEATURE_FLAG,
    MAIN_GROUP,
    MESSAGE_MAX_LENGTH,
    AlternativeReason,
    AlternativeStatus,
    Attendance,
    EventKind,
    EventSource,
    IdPhoto,
    IncidentCategory,
    IncidentSeverity,
    MessageKind,
    OnUnavailable,
    ProctorAssignment,
    ProctoringConfig,
    ProctoringConsent,
    ProctoringEvent,
    ProctoringIncident,
    ProctoringMessage,
    ProctoringRecording,
    ProctoringSession,
    ProctorKind,
    RecordingStatus,
)
from .storage import KEY_PREFIX, MEDIA_URL_TTL_SECONDS, delete_quietly, get_storage

logger = logging.getLogger(__name__)

#: Wersja informacji i oświadczenia o nadzorze. Zmiana treści = zmiana tej stałej: zgody złożone pod
#: starą wersją przestają wystarczać (uczeń składa zgodę jeszcze raz), a wpisy dowodowe dalej mówią,
#: co obowiązywało wtedy – ta sama zasada, co ``apps.accounts.consents.TERMS_VERSION``.
CONSENT_VERSION = "1.0 z 4 października 2026"

#: Oświadczenie przy polu wyboru. Skrót SHA-256 **polskiego brzmienia** z wersją trafia do dowodu.
CONSENT_STATEMENT = gettext_lazy(
    "Zapoznałem/-am się z informacją o nadzorze zdalnym tego etapu i wyrażam zgodę na przekazywanie "
    "obrazu z mojej kamery (oraz – jeśli etap tego wymaga – mojego ekranu i dźwięku) osobom "
    "nadzorującym na zasadach opisanych powyżej."
)
CONSENT_STATEMENT_SOURCE = (
    "Zapoznałem/-am się z informacją o nadzorze zdalnym tego etapu i wyrażam zgodę na przekazywanie "
    "obrazu z mojej kamery (oraz – jeśli etap tego wymaga – mojego ekranu i dźwięku) osobom "
    "nadzorującym na zasadach opisanych powyżej."
)

#: Rodziny przeglądarek zapisywane w wyniku sprawdzenia – nic dokładniejszego (wersja, system,
#: rozdzielczość) nie wchodzi do bazy: to byłby odcisk urządzenia, a do nadzoru nie jest potrzebny.
BROWSER_FAMILIES = ("chrome", "edge", "firefox", "safari", "opera", "other")
#: Klucze wyniku sprawdzenia (wartości logiczne). Reszta tego, co przyśle przeglądarka, przepada.
CHECK_KEYS = ("webrtc", "camera", "microphone", "screen")
#: Oświadczenie niepełnoletniego o nadzorze **konkretnie** (dodatkowe pole przy zgodzie). Ogólna
#: zgoda opiekuna z rejestracji dotyczy udziału i danych, a nie obrazu z domu – to pole mówi, że
#: opiekun zna tę informację. Podstawę prawną opisuje nota DPIA (podręcznik organizatora § 10m).
GUARDIAN_STATEMENT = gettext_lazy(
    "Mój rodzic lub opiekun prawny zna tę informację o nadzorze zdalnym i zgadza się na niego."
)
GUARDIAN_STATEMENT_SOURCE = (
    "Mój rodzic lub opiekun prawny zna tę informację o nadzorze zdalnym i zgadza się na niego."
)

#: Zdarzenia, które wolno zgłosić przeglądarce ucznia. Kamera i ekran – z webhooków serwera.
CLIENT_EVENTS = (EventKind.STREAM_DROPPED, EventKind.RECONNECTED, EventKind.CONNECT_FAILED)
#: Powody nieudanego połączenia, które liczą się do „kontynuuj bez nadzoru”. Kamera odmówiona albo
#: odłączona **nie** jest awarią serwera – to sprawa na alternatywę z decyzją koordynatora.
NETWORK_FAILURES = ("token", "connect", "publish")
CAMERA_FAILURES = ("camera",)
#: Powody pracy bez nadzoru (``ProctoringSession.unproctored_reason``).
UNPROCTORED_NOT_CONFIGURED = "not_configured"
UNPROCTORED_SERVER_UNREACHABLE = "server_unreachable"
UNPROCTORED_CONNECT_FAILURES = "connect_failures"
#: Grupa ``m`` (uczniowie bez przydziału) większa niż tyle osób – ostrzeżenie na ekranie koordynatora:
#: jeden pokój LiveKit żyje na jednym węźle, więc duża grupa bez przydziału to jeden gorący węzeł.
MAIN_GROUP_WARNING = 250
#: Najwięcej bajtów zdjęcia dokumentu (JPEG 640×480 z konsoli waży ~60 KB).
ID_PHOTO_MAX_BYTES = 300 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"
#: Rozmiary strony siatki nadzorującego.
PAGE_SIZES = (12, 16, 24)
#: Puls konsoli ucznia – po tylu sekundach ciszy lista nadzorującego pokazuje „brak sygnału”.
STALE_SECONDS = 90


# --- przełączniki ----------------------------------------------------------------------------------


def enabled(competition) -> bool:
    return competition is not None and competition.has_feature(FEATURE_FLAG)


def configured() -> bool:
    from apps.webinars import livekit

    return livekit.configured()


def config_for(stage) -> ProctoringConfig | None:
    """Ustawienia nadzoru etapu, gdy jest **włączony** – inaczej ``None`` (etap bez nadzoru)."""
    if stage is None:
        return None
    config = ProctoringConfig.objects.filter(stage=stage, enabled=True).select_related(
        "stage__edition__competition"
    )
    return config.first()


def stage_competition(stage):
    """Konkurs etapu bez zapytania, gdy kontekst żądania to ten sam konkurs (ścieżki mierzone testami)."""
    from apps.tenancy.context import current_competition

    current = current_competition()
    if current is None:
        return stage.edition.competition
    # Etap z załadowaną edycją sprawdzamy darmowo; bez niej ufamy kontekstowi – etap przyszedł
    # z zawężonego querysetu konkursu żądania, a cudzy etap i tak skończył się wcześniej 404.
    edition = stage._state.fields_cache.get("edition")
    if edition is not None and edition.competition_id != current.pk:
        return edition.competition
    return current


def _not_found(message: str = "Nie ma takiej sesji nadzoru.") -> DomainError:
    return DomainError(message, "PROCTORING_NOT_FOUND", 404)


def _unavailable() -> DomainError:
    return DomainError(
        _("Serwer nadzoru nie odpowiada. Spróbuj za chwilę."), "PROCTORING_LIVEKIT_UNAVAILABLE", 502
    )


# --- pseudonimy i nazwy ----------------------------------------------------------------------------


def _hmac(label: str) -> str:
    key = f"proctoring-livekit:{settings.SECRET_KEY}".encode()
    return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()[:20]


def student_identity(stage, participant) -> str:
    """Pseudonim ucznia w pokoju – **osobny na etap**: nie łączy jego wejść między etapami."""
    return "p-" + _hmac(f"s:{stage.pk}:{participant.pk}")


def proctor_identity(stage, user) -> str:
    return "x-" + _hmac(f"x:{stage.pk}:{user.pk}")


def group_for(participant, proctor: ProctorAssignment | None = None) -> str:
    """Grupa (pokój) ucznia.

    - przydział do koordynatora albo członka komisji → ``a<przydział>`` (także uczeń delegacji:
      członek komisji nie może dostać tokenu do pokoju całej delegacji, bo zobaczyłby cudzych uczniów),
    - uczeń delegacji bez przydziału albo przydzielony opiekunowi tej delegacji → ``d<delegacja>``,
    - pozostali → ``m`` (bez przydziału; widzą ich koordynatorzy).

    Pokój na przydział, a nie jeden wspólny: (1) członek komisji dostaje token wyłącznie do pokoi
    swoich uczniów – nie zasubskrybuje cudzego ucznia nawet przerobionym skryptem, (2) pokój w
    LiveKit żyje na jednym węźle, a etap online bywa liczony w setkach – pokoje po kilkanaście–
    kilkadziesiąt osób rozkładają się na węzły klastra.
    """
    if proctor is not None and proctor.kind != ProctorKind.LEADER:
        return f"a{proctor.pk}"
    delegation_id = delegations.participant_delegation_id(participant)
    if delegation_id:
        return f"d{delegation_id}"
    return f"a{proctor.pk}" if proctor is not None else MAIN_GROUP


def session_group(session) -> str:
    return group_for(session.participant, session.proctor)


def group_delegation(group: str) -> int | None:
    if group.startswith("d") and group[1:].isdigit():
        return int(group[1:])
    return None


def group_label(group: str) -> str:
    delegation_id = group_delegation(group)
    if delegation_id:
        return delegations.delegation_label(delegation_id)
    if group.startswith("a") and group[1:].isdigit():
        assignment = ProctorAssignment.objects.select_related("user").filter(pk=int(group[1:])).first()
        if assignment is not None:
            from apps.competitions.jitsi_jwt import short_name

            return _("uczniowie: %(name)s") % {"name": short_name(assignment.user) or assignment.user_id}
    return _("uczniowie bez przydziału")


def student_label(participant) -> str:
    """Podpis kafla nadzorującego: „Imię N.” i kod publiczny – tyle, ile trzeba, żeby rozpoznać."""
    from apps.competitions.jitsi_jwt import short_name

    name = short_name(participant.user) or ""
    return f"{name} · {participant.public_code}" if name else participant.public_code


def _audit(actor, action: str, obj, extra: dict | None = None, request=None) -> None:
    from apps.core.models import audit

    audit(actor, action, obj, extra or {}, request=request)


def log_event(session, kind, source, *, actor=None, detail=None, at=None) -> ProctoringEvent:
    return ProctoringEvent.objects.create(
        session=session,
        kind=kind,
        source=source,
        actor=actor if getattr(actor, "pk", None) else None,
        detail=detail or {},
        at=at or timezone.now(),
    )


# --- role -----------------------------------------------------------------------------------------


def is_coordinator(user, competition) -> bool:
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    return has_role(user, competition, CompetitionRole.COORDINATOR)


def committee_ok(user, competition) -> bool:
    from apps.competitions.video_rooms import committee_member

    return committee_member(user, competition) is not None


def appeals_ok(user, competition) -> bool:
    """Komisja odwoławcza tego konkursu (rola i aktywny profil) – odbiorca raportów i nagrań."""
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role
    from apps.appeals.services import appeals_committee_profile

    return has_role(user, competition, CompetitionRole.APPEALS) and (
        appeals_committee_profile(user, competition) is not None
    )


def can_review(user, competition) -> bool:
    """Raport ucznia, eksport i nagrania: koordynator albo komisja odwoławcza."""
    return is_coordinator(user, competition) or appeals_ok(user, competition)


@dataclass
class ProctorScope:
    """Zakres jednego nadzorującego na jednym etapie – policzony dla bieżącego żądania."""

    user: object
    stage: object
    config: ProctoringConfig
    kind: str
    assignment: ProctorAssignment | None = None
    delegation_id: int | None = None
    groups: set[str] | None = field(default=None)  # ``None`` = wszystkie

    @property
    def is_coordinator(self) -> bool:
        return self.kind == ProctorKind.COORDINATOR

    def sessions(self):
        """Sesje w zakresie – zawężone **zapytaniem**, a nie warunkiem w widoku."""
        rows = ProctoringSession.objects.filter(stage=self.stage)
        if self.kind == ProctorKind.COORDINATOR:
            return rows
        if self.kind == ProctorKind.LEADER:
            return rows.filter(group=f"d{self.delegation_id}")
        return rows.filter(proctor=self.assignment)

    def allowed_groups(self) -> list[str]:
        if self.kind == ProctorKind.LEADER:
            return [f"d{self.delegation_id}"]
        groups = set(self.sessions().values_list("group", flat=True).distinct())
        return sorted(groups) or [MAIN_GROUP]

    def allows_group(self, group: str) -> bool:
        if self.kind == ProctorKind.LEADER:
            return group == f"d{self.delegation_id}"
        if self.kind == ProctorKind.COORDINATOR:
            return group == MAIN_GROUP or (group[:1] in ("a", "d") and group[1:].isdigit())
        return group in self.allowed_groups()


def proctor_scope(user, stage) -> ProctorScope | None:
    """Zakres nadzoru osoby na etapie albo ``None`` (404). Rola sprawdzana **dziś**, nie przy przydziale."""
    competition = stage.edition.competition
    config = config_for(stage)
    if config is None or user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    if is_coordinator(user, competition):
        return ProctorScope(user=user, stage=stage, config=config, kind=ProctorKind.COORDINATOR)
    assignment = ProctorAssignment.objects.filter(stage=stage, user=user).first()
    if assignment is None:
        return None
    if assignment.kind == ProctorKind.COMMITTEE and committee_ok(user, competition):
        return ProctorScope(
            user=user, stage=stage, config=config, kind=ProctorKind.COMMITTEE, assignment=assignment
        )
    if assignment.kind == ProctorKind.LEADER:
        current = delegations.leader_delegation_id(user, competition, stage)
        # Opiekun odwołany albo przeniesiony do innego kraju traci nadzór od razu – przydział
        # z migawką delegacji nie wystarcza, liczy się delegacja, którą ta osoba prowadzi dziś.
        if current is not None and current == assignment.delegation_id:
            return ProctorScope(
                user=user,
                stage=stage,
                config=config,
                kind=ProctorKind.LEADER,
                assignment=assignment,
                delegation_id=current,
            )
    return None


def stages_for_proctor(user, competition) -> list:
    """Etapy z włączonym nadzorem, na których ta osoba ma dziś zakres."""
    from apps.competitions.models import Stage

    if not enabled(competition):
        return []
    stages = (
        Stage.objects.for_competition(competition)
        .filter(proctoring__enabled=True)
        .select_related("edition__competition")
        .order_by("-opens_at", "id")
    )
    return [stage for stage in stages if proctor_scope(user, stage) is not None]


# --- uczeń: sesja, zgoda, sprawdzenie ---------------------------------------------------------------


def entry_for(participant, stage):
    """Zgłoszenie ucznia do etapu, o ile nie jest zdyskwalifikowany – inaczej ``None``."""
    from apps.competitions.models import StageEntry, StageEntryStatus

    if participant is None:
        return None
    return (
        StageEntry.objects.filter(participant=participant, stage=stage)
        .exclude(status=StageEntryStatus.DISQUALIFIED)
        .first()
    )


def session_for(stage, participant, *, create: bool = True) -> ProctoringSession | None:
    # ``participant`` i ``proctor`` – czyta je zaraz grupa pokoju i zgoda (bez leniwych zapytań).
    found = (
        ProctoringSession.objects.select_related("participant__user", "proctor", "stage")
        .filter(stage=stage, participant=participant)
        .first()
    )
    if found is not None or not create:
        return found
    try:
        with transaction.atomic():
            return ProctoringSession.objects.create(
                stage=stage,
                participant=participant,
                identity=student_identity(stage, participant),
                group=group_for(participant),
            )
    except IntegrityError:  # dwa żądania naraz
        return ProctoringSession.objects.get(stage=stage, participant=participant)


def guardian_requirement(participant):
    """``(wymagana, wpis)`` – zgoda opiekuna złożona online dla niepełnoletniego (reguła platformy)."""
    from apps.accounts.guardian import confirmed_record, requires_guardian_consent

    if not requires_guardian_consent(participant):
        return False, None
    return True, confirmed_record(participant)


def consent_terms(config) -> dict:
    """Ustawienia etapu, na które uczeń się zgadza – wchodzą do skrótu dowodu zgody.

    Zgoda na „obraz z kamery na żywo” nie jest zgodą na nagranie ani na dźwięk: zmiana któregoś
    z tych ustawień po zgodzie zmienia skrót, więc dotychczasowa zgoda przestaje być ważna i uczeń
    składa ją jeszcze raz, widząc nową informację.
    """
    return {
        "record": bool(config.record),
        "microphone": bool(config.require_microphone),
        "screen": bool(config.require_screen_share),
        "id_photo": str(config.id_photo),
    }


def consent_digest(config, *, minor: bool = False) -> str:
    terms = ",".join(f"{key}={value}" for key, value in sorted(consent_terms(config).items()))
    parts = [CONSENT_VERSION, CONSENT_STATEMENT_SOURCE, terms]
    if minor:
        parts.append(GUARDIAN_STATEMENT_SOURCE)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _config_of(session, config=None):
    return config if config is not None else ProctoringConfig.objects.get(stage_id=session.stage_id)


def active_consent(session, config=None) -> ProctoringConsent | None:
    """Ważna zgoda: obecna wersja, **te same ustawienia etapu** i – u niepełnoletniego – zgoda
    opiekuna, która dziś nadal obowiązuje (wycofanie zgody opiekuna gasi zgodę na nadzór)."""
    if session is None:
        return None
    config = _config_of(session, config)
    required, _guardian = guardian_requirement(session.participant)
    consent = (
        session.consents.filter(
            withdrawn_at__isnull=True,
            version=CONSENT_VERSION,
            text_sha256=consent_digest(config, minor=required),
        )
        .select_related("guardian_record")
        .first()
    )
    if consent is None:
        return None
    if required and (consent.guardian_record is None or consent.guardian_record.withdrawn_at is not None):
        return None
    return consent


def give_consent(session, *, user, config=None, guardian_statement: bool = False, request=None):
    """Zgoda ucznia. Niepełnoletni – przy **potwierdzonej online** zgodzie opiekuna i z oświadczeniem
    o nadzorze (``guardian_statement``)."""
    from apps.core.models import client_ip

    config = _config_of(session, config)
    required, guardian = guardian_requirement(session.participant)
    if required and guardian is None:
        raise DomainError(
            _(
                "Najpierw potrzebna jest zgoda Twojego rodzica lub opiekuna prawnego, potwierdzona "
                "online. Poproś o nią w panelu, w zakładce „Zgody”."
            ),
            "PROCTORING_GUARDIAN_REQUIRED",
            409,
        )
    if required and not guardian_statement:
        raise DomainError(
            _("Zaznacz też oświadczenie o wiedzy i zgodzie rodzica lub opiekuna prawnego."),
            "PROCTORING_GUARDIAN_STATEMENT",
            400,
        )
    existing = active_consent(session, config)
    if existing is not None:
        return existing
    consent = ProctoringConsent.objects.create(
        session=session,
        version=CONSENT_VERSION,
        text_sha256=consent_digest(config, minor=required),
        terms=consent_terms(config),
        ip=client_ip(request) if request is not None else None,
        guardian_record=guardian,
        guardian_statement=required,
    )
    log_event(session, EventKind.CONSENT_GIVEN, EventSource.CLIENT, detail={"version": CONSENT_VERSION})
    _audit(user, "proctoring.consent_given", session, {"version": CONSENT_VERSION}, request=request)
    return consent


def withdraw_consent(session, *, user, request=None) -> int:
    """Wycofanie zgody – uczeń znika z pokoju od razu (token ważny jeszcze kilka minut by nie
    wystarczył: połączenie raz nawiązane trwa), nagrywanie staje, dalej – decyzja koordynatora."""
    count = session.consents.filter(withdrawn_at__isnull=True).update(withdrawn_at=timezone.now())
    if count:
        kick_student(session)
        log_event(session, EventKind.CONSENT_WITHDRAWN, EventSource.CLIENT)
        _audit(user, "proctoring.consent_withdrawn", session, request=request)
    return count


# --- polecenia do LiveKit: wyproszenie z pokoju -------------------------------------------------------


def kick_student(session) -> None:
    """Wyprasza ucznia z pokoju jego grupy i zatrzymuje jego aktywne nagrania. Awaria – w logu."""
    if not configured():
        return
    from apps.webinars import livekit

    config = (
        ProctoringConfig.objects.select_related("stage__edition__competition")
        .filter(stage_id=session.stage_id)
        .first()
    )
    if config is None:
        return
    for recording in session.recordings.filter(status=RecordingStatus.ACTIVE):
        try:
            livekit_api.stop_egress(recording.egress_id)
        except livekit.LiveKitUnavailable, livekit.LiveKitError:
            logger.warning("Nadzór: nie udało się zatrzymać nagrania %s.", recording.pk)
    try:
        livekit.remove_participant(config.room_name(session.group), session.identity)
    except livekit.LiveKitUnavailable, livekit.LiveKitError:
        logger.info("Nadzór: uczeń sesji %s nie był w pokoju.", session.pk)


def kick_proctor(stage, user, groups) -> int:
    """Wyprasza nadzorującego z pokoi grup ``groups`` – po odebraniu przydziału albo delegacji.

    Token żyje kilka minut, ale **połączenie** raz nawiązane trwa – bez tego odwołany opiekun
    oglądałby uczniów do końca etapu.
    """
    if not configured():
        return 0
    from apps.webinars import livekit

    config = (
        ProctoringConfig.objects.select_related("stage__edition__competition").filter(stage=stage).first()
    )
    if config is None:
        return 0
    identity = proctor_identity(stage, user)
    removed = 0
    for group in sorted(set(groups)):
        try:
            livekit.remove_participant(config.room_name(group), identity)
            removed += 1
        except livekit.LiveKitUnavailable, livekit.LiveKitError:
            continue
    return removed


def record_check(session, config, result: dict, *, user, request=None) -> bool:
    """Wynik sprawdzenia sprzętu: wyłącznie wartości logiczne z listy i rodzina przeglądarki."""
    clean = {key: bool(result.get(key)) for key in CHECK_KEYS}
    browser = str(result.get("browser") or "other").lower()
    clean["browser"] = browser if browser in BROWSER_FAMILIES else "other"
    passed = (
        clean["webrtc"]
        and clean["camera"]
        and (clean["microphone"] or not config.require_microphone)
        and (clean["screen"] or not config.require_screen_share)
    )
    session.check_result = clean
    session.check_passed_at = timezone.now() if passed else None
    session.save(update_fields=["check_result", "check_passed_at"])
    log_event(
        session,
        EventKind.CHECK_PASSED if passed else EventKind.CHECK_FAILED,
        EventSource.CLIENT,
        detail=clean,
    )
    return passed


def id_photo_key(session) -> str:
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    config = session.stage.proctoring
    slug = session.stage.edition.competition.slug
    return f"{KEY_PREFIX}{slug}/{config.room_key}/{session.identity}/id-{stamp}.jpg"


def save_id_photo(session, config, data: bytes, *, user, request=None) -> None:
    """Zdjęcie dokumentu z konsoli: JPEG (sygnatura, nie nagłówek), ≤ 300 KB, prywatny bucket."""
    if config.id_photo == IdPhoto.OFF:
        raise DomainError(_("Ten etap nie zbiera zdjęcia dokumentu."), "PROCTORING_NO_ID_PHOTO", 409)
    if not data or len(data) > ID_PHOTO_MAX_BYTES or not data.startswith(JPEG_MAGIC):
        raise DomainError(_("Zdjęcie nie zostało zapisane. Spróbuj jeszcze raz."), "PROCTORING_ID_PHOTO", 400)
    old = session.id_photo_key
    key = id_photo_key(session)
    get_storage().put(key, data, "image/jpeg")
    session.id_photo_key = key
    session.id_photo_at = timezone.now()
    session.save(update_fields=["id_photo_key", "id_photo_at"])
    if old and old != key:
        delete_quietly(old)
    log_event(session, EventKind.ID_PHOTO, EventSource.CLIENT)
    _audit(user, "proctoring.id_photo", session, request=request)


# --- uczeń: stan i bramka --------------------------------------------------------------------------


def photo_done(session, config) -> bool:
    return config.id_photo != IdPhoto.REQUIRED or bool(session.id_photo_key)


def is_ready(session, config) -> bool:
    """Czy etap jest dla ucznia otwarty: zgoda i (nadawanie potwierdzone albo wyjątek z decyzji).

    Raz otwarty etap **nie zamyka się** przy zerwanym strumieniu – zerwanie jest zdarzeniem
    w dzienniku i ostrzeżeniem, a nie odebraniem pracy w połowie zawodów.
    """
    if session is None:
        return False
    if session.stage.is_interview:
        return interview_ready(session, config)  # rozmowa LiveKit (STAGE-LK-01)
    if session.alternative_approved:
        return True
    if active_consent(session, config) is None:
        return False
    if session.started_at is not None:
        return True
    return session.unproctored_at is not None and config.on_unavailable == OnUnavailable.ALLOW


def step(session, config) -> str:
    """Krok konsoli ucznia: ``consent`` → ``check`` → ``photo`` → ``start`` → ``live``."""
    if session.alternative_approved:
        return "alternative"
    if active_consent(session, config) is None:
        return "consent"
    if session.check_passed_at is None:
        return "check"
    if not photo_done(session, config):
        return "photo"
    if session.stage.is_interview:
        return "interview"  # rozmowa LiveKit – dalej przez panel („Dołącz do rozmowy”)
    if session.started_at is None and session.unproctored_at is None:
        return "start"
    return "live"


#: Odpowiedzi bramki (``gate_decision``): ``None`` = przepuść.
GATE_CONSOLE = "console"  # uczeń etapu bez gotowej sesji – do konsoli nadzoru
GATE_LOGIN = "login"  # osoba niezalogowana w oknie etapu
GATE_DENIED = "denied"  # zalogowany bez zgłoszenia do etapu i bez roli personelu


def is_staff(user, competition) -> bool:
    """Personel, który w oknie etapu z nadzorem dalej widzi treść: koordynator, aktywna komisja."""
    return (
        bool(getattr(user, "is_superuser", False))
        or is_coordinator(user, competition)
        or committee_ok(user, competition)
    )


def gate_decision(user, stage, competition, now=None) -> str | None:
    """Bramka treści etapu z nadzorem: kto w **oknie etapu** może zobaczyć zadania.

    - konkurs bez flagi albo etap bez nadzoru – przepuść (bez flagi: zero zapytań),
    - niezalogowany w oknie – ``login``: treść zadań nie może wyjść poza nadzór anonimowym adresem,
    - zalogowany bez (niezdyskwalifikowanego) zgłoszenia – ``denied``, chyba że to personel
      (koordynator, komisja) – ci zachowują dostęp,
    - uczeń etapu w swoim oknie bez gotowej sesji – ``console``.

    Poza oknem bramka nie wtrąca się: przed otwarciem treść zamykają serwisy etapu (i TZ-01), po
    zamknięciu nie ma już czego nadzorować.
    """
    from apps.accounts.services import participant_for

    if not enabled(competition):
        return None
    config = config_for(stage)
    if config is None:
        return None
    now = now or timezone.now()
    if user is None or not getattr(user, "is_authenticated", False):
        opens, closes = windows.envelope(stage)
        return GATE_LOGIN if opens <= now < closes else None
    participant = participant_for(user, competition)
    entry = entry_for(participant, stage) if participant is not None else None
    if entry is None:
        if is_staff(user, competition):
            return None
        opens, closes = windows.envelope(stage)
        return GATE_DENIED if opens <= now < closes else None
    opens, closes = windows.effective_window(stage, participant)
    if not (opens <= now < closes):
        return None
    session = session_for(stage, participant, create=False)
    return None if is_ready(session, config) else GATE_CONSOLE


def assert_stage_access(stage, entry, now=None) -> None:
    """Druga linia obrony w serwisach (wysyłka rozwiązania, start testu) – ta sama reguła ucznia,
    co bramka adresów: w oknie bez gotowej sesji nadzoru – ``DomainError`` 403.

    Bez flagi konkursu zero zapytań: flagę czytamy z konkursu kontekstu, **zanim** dotkniemy etapu
    (``stage`` może być funkcją bez argumentów – np. ``lambda: quiz.stage`` – liczoną dopiero wtedy).
    """
    from apps.tenancy.context import current_competition

    current = current_competition()
    if current is not None and not enabled(current):
        return
    if callable(stage):
        stage = stage()
    competition = stage_competition(stage)
    if not enabled(competition):
        return
    config = config_for(stage)
    if config is None:
        return
    participant = entry.participant
    now = now or timezone.now()
    opens, closes = windows.effective_window(stage, participant)
    if not (opens <= now < closes):
        return
    if not is_ready(session_for(stage, participant, create=False), config):
        raise DomainError(
            _("Ten etap jest nadzorowany zdalnie. Najpierw włącz nadzór w konsoli nadzoru."),
            "PROCTORING_REQUIRED",
            403,
        )


def student_token(session, config, *, user, request=None, now=None) -> dict:
    """Token ucznia: zgoda, sprzęt, zdjęcie (gdy wymagane) i okno – dopiero wtedy."""
    now = now or timezone.now()
    if not configured():
        raise _unavailable()
    if entry_for(session.participant, session.stage) is None:
        raise _not_found()
    if active_consent(session, config) is None:
        raise DomainError(_("Najpierw wyraź zgodę na nadzór."), "PROCTORING_CONSENT_REQUIRED", 409)
    if session.check_passed_at is None:
        raise DomainError(_("Najpierw sprawdź sprzęt."), "PROCTORING_CHECK_REQUIRED", 409)
    if not photo_done(session, config):
        raise DomainError(_("Najpierw zrób zdjęcie dokumentu."), "PROCTORING_PHOTO_REQUIRED", 409)
    opens, closes = windows.student_window(session.stage, session.participant)
    if now < opens:
        local = timezone.localtime(opens)
        raise DomainError(
            _("Nadzór będzie można włączyć od %(when)s.") % {"when": local.strftime("%d.%m.%Y, %H:%M")},
            "PROCTORING_NOT_YET",
            409,
        )
    if now >= closes:
        raise DomainError(_("Czas tego etapu minął."), "PROCTORING_OVER", 409)
    group = session_group(session)
    if group != session.group:
        session.group = group
        session.save(update_fields=["group"])
    room = config.room_name(group)
    video = livekit_api.student_grants(
        room, screen=config.require_screen_share, microphone=config.require_microphone
    )
    # Pusta nazwa: inni uczniowie w pokoju grupy dostają od serwera listę uczestników – widzą w niej
    # wyłącznie pseudonimy. Nadzorujący rozpoznaje ucznia z listy platformy, nie z pokoju.
    token = livekit_api.access_token(identity=session.identity, name="", video=video)
    _audit(user, "proctoring.joined", session, {"role": "student", "group": group}, request=request)
    return {
        "url": _ws_url(),
        "token": token,
        "identity": session.identity,
        "screen": config.require_screen_share,
        "microphone": config.require_microphone,
    }


def _ws_url() -> str:
    from apps.webinars import livekit

    return livekit.ws_url()


def confirm_started(session, config, *, user, request=None) -> ProctoringSession:
    """Serwer **sprawdza w LiveKit**, że kamera ucznia nadaje – dopiero wtedy etap się otwiera.

    Bez tego wystarczyłoby wysłać „nadaję” z konsoli, nie włączając kamery. Ekran – gdy etap go
    wymaga – też musi być udostępniony.
    """
    if active_consent(session, config) is None:
        raise DomainError(_("Najpierw wyraź zgodę na nadzór."), "PROCTORING_CONSENT_REQUIRED", 409)
    room = config.room_name(session.group)
    try:
        info = livekit_api.get_participant(room, session.identity)
    except livekit_api.LiveKitUnavailable as exc:
        raise _unavailable() from exc
    except livekit_api.LiveKitError as exc:
        raise DomainError(
            _("Serwer nadzoru jeszcze nie widzi Twojej kamery. Poczekaj chwilę i spróbuj ponownie."),
            "PROCTORING_NOT_LIVE",
            409,
        ) from exc
    sources = livekit_api.live_sources(info)
    if "camera" not in sources or (config.require_screen_share and "screen" not in sources):
        raise DomainError(
            _("Serwer nadzoru jeszcze nie widzi Twojej kamery. Poczekaj chwilę i spróbuj ponownie."),
            "PROCTORING_NOT_LIVE",
            409,
        )
    now = timezone.now()
    first = session.started_at is None
    session.started_at = session.started_at or now
    session.connected = True
    session.camera_live = True
    session.screen_live = "screen" in sources
    session.last_seen_at = now
    session.save(update_fields=["started_at", "connected", "camera_live", "screen_live", "last_seen_at"])
    if first:
        log_event(session, EventKind.STARTED, EventSource.SYSTEM, detail={"sources": sorted(sources)})
        _audit(user, "proctoring.started", session, request=request)
    return session


def failure_threshold() -> int:
    return max(1, int(getattr(settings, "PROCTORING_UNPROCTORED_AFTER_FAILURES", 3)))


def network_failures(session) -> int:
    """Zgłoszone przez konsolę nieudane połączenia z serwerem (token, połączenie, publikacja)."""
    return session.events.filter(kind=EventKind.CONNECT_FAILED, detail__reason__in=NETWORK_FAILURES).count()


def server_reachability(session, config) -> bool:
    """Czy **platforma** sięga do LiveKit (``GetParticipant``) – niezależnie od tego, co mówi uczeń."""
    if not configured():
        return False
    try:
        livekit_api.get_participant(config.room_name(session.group), session.identity)
    except livekit_api.LiveKitError:
        return True  # serwer odpowiedział (np. „nie ma takiego uczestnika”) – działa
    except livekit_api.LiveKitUnavailable:
        return False
    return True


def unproctored_reason(session, config) -> str | None:
    """Powód, dla którego wolno dziś pracować bez nadzoru – albo ``None`` (nie wolno).

    Wyłącznie awaria **po stronie nadzoru**: serwer nieskonfigurowany, serwer nieosiągalny dla
    platformy albo co najmniej ``PROCTORING_UNPROCTORED_AFTER_FAILURES`` zgłoszonych nieudanych
    połączeń (np. sieć szkoły blokuje WebRTC). Kamera odmówiona albo odłączona – nie: to sprawa na
    alternatywę z decyzją koordynatora, bo inaczej „wyłącz kamerę i kliknij” omijałoby nadzór.
    """
    if not configured():
        return UNPROCTORED_NOT_CONFIGURED
    if not server_reachability(session, config):
        return UNPROCTORED_SERVER_UNREACHABLE
    if network_failures(session) >= failure_threshold():
        return UNPROCTORED_CONNECT_FAILURES
    return None


def continue_unproctored(session, config, *, user, request=None) -> ProctoringSession:
    """„Kontynuuj bez nadzoru” – tylko przy ``on_unavailable=allow``, ze zgodą i z powodem z
    :func:`unproctored_reason`. Powód i ``server_reachable`` idą do sesji, dziennika, raportu i CSV."""
    if config.on_unavailable != OnUnavailable.ALLOW:
        raise DomainError(
            _("Ten etap wymaga działającego nadzoru. Skontaktuj się z organizatorem."),
            "PROCTORING_BLOCKED",
            409,
        )
    if active_consent(session, config) is None:
        raise DomainError(_("Najpierw wyraź zgodę na nadzór."), "PROCTORING_CONSENT_REQUIRED", 409)
    if session.unproctored_at is not None:
        return session
    reason = unproctored_reason(session, config)
    if reason is None:
        raise DomainError(
            _(
                "Serwer nadzoru działa – spróbuj połączyć się jeszcze raz. Jeśli nie możesz użyć "
                "kamery, poproś o inną formę nadzoru."
            ),
            "PROCTORING_SERVER_WORKS",
            409,
        )
    reachable = reason == UNPROCTORED_CONNECT_FAILURES
    session.unproctored_at = timezone.now()
    session.unproctored_reason = reason
    session.save(update_fields=["unproctored_at", "unproctored_reason"])
    log_event(
        session,
        EventKind.LIVEKIT_UNAVAILABLE,
        EventSource.SYSTEM,
        detail={"server_reachable": reachable, "configured": configured(), "reason": reason},
    )
    log_event(session, EventKind.UNPROCTORED, EventSource.SYSTEM, detail={"reason": reason})
    _audit(user, "proctoring.unproctored", session, {"reason": reason}, request=request)
    return session


def client_event(session, kind: str, *, heartbeat: bool = False, reason: str = "") -> None:
    """Zgłoszenie konsoli: zerwanie / powrót strumienia, nieudane połączenie albo puls (sam czas)."""
    now = timezone.now()
    if heartbeat:
        ProctoringSession.objects.filter(pk=session.pk).update(last_seen_at=now)
        return
    if kind not in CLIENT_EVENTS:
        raise DomainError("Nieznane zdarzenie.", "PROCTORING_EVENT", 400)
    detail = {}
    if kind == EventKind.CONNECT_FAILED:
        detail["reason"] = reason if reason in (*NETWORK_FAILURES, *CAMERA_FAILURES) else "connect"
    log_event(session, kind, EventSource.CLIENT, at=now, detail=detail)
    ProctoringSession.objects.filter(pk=session.pk).update(last_seen_at=now)


def messages_for_student(session, after: int = 0) -> list[dict]:
    rows = session.messages.filter(pk__gt=max(0, int(after))).order_by("pk")[:50]
    return [{"id": row.pk, "kind": row.kind, "body": row.body, "at": row.sent_at.isoformat()} for row in rows]


def ack_message(session, pk) -> bool:
    now = timezone.now()
    updated = ProctoringMessage.objects.filter(session=session, pk=pk, seen_at__isnull=True).update(
        seen_at=now
    )
    if updated:
        log_event(session, EventKind.MESSAGE_SEEN, EventSource.CLIENT, detail={"message": int(pk)}, at=now)
    return bool(updated)


def request_alternative(session, reason: str, note: str, *, user, request=None) -> ProctoringSession:
    """Prośba ucznia bez kamery o inną formę nadzoru – powód z listy, krótka uwaga."""
    if reason not in AlternativeReason.values:
        raise DomainError(_("Wybierz powód z listy."), "PROCTORING_ALTERNATIVE_REASON", 400)
    if session.alternative_status == AlternativeStatus.APPROVED:
        return session
    session.alternative_status = AlternativeStatus.REQUESTED
    session.alternative_reason = reason
    session.alternative_note = (note or "").strip()[:300]
    session.alternative_requested_at = timezone.now()
    session.save(
        update_fields=[
            "alternative_status",
            "alternative_reason",
            "alternative_note",
            "alternative_requested_at",
        ]
    )
    log_event(
        session, EventKind.ALTERNATIVE, EventSource.CLIENT, detail={"status": "requested", "reason": reason}
    )
    _audit(user, "proctoring.alternative_requested", session, {"reason": reason}, request=request)
    return session


def decide_alternative(session, *, approve: bool, decision: str, actor, request=None) -> ProctoringSession:
    """Decyzja koordynatora (np. „nadzór telefoniczny o 9:00”). Zatwierdzona – etap otwarty bez kamery."""
    session.alternative_status = AlternativeStatus.APPROVED if approve else AlternativeStatus.REJECTED
    session.alternative_decision = (decision or "").strip()[:300]
    session.alternative_decided_by = actor
    session.alternative_decided_at = timezone.now()
    session.save(
        update_fields=[
            "alternative_status",
            "alternative_decision",
            "alternative_decided_by",
            "alternative_decided_at",
        ]
    )
    status_value = "approved" if approve else "rejected"
    log_event(
        session, EventKind.ALTERNATIVE, EventSource.PROCTOR, actor=actor, detail={"status": status_value}
    )
    _audit(actor, "proctoring.alternative_decided", session, {"status": status_value}, request=request)
    return session


# --- nadzorujący: lista, token, czynności ------------------------------------------------------------


def session_in_scope(scope: ProctorScope, pk) -> ProctoringSession:
    """Sesja z zakresu nadzorującego albo 404 – cudzy uczeń (inna delegacja) nie istnieje."""
    found = (
        scope.sessions().select_related("participant__user", "stage").filter(pk=pk).first()
        if str(pk).isdigit()
        else None
    )
    if found is None:
        raise _not_found()
    return found


def _status(session, now) -> str:
    if session.alternative_approved:
        return "alternative"
    if session.unproctored_at is not None and session.started_at is None:
        return "unproctored"
    if session.started_at is None:
        return "waiting"
    if not session.connected or not session.camera_live:
        return "dropped"
    if session.last_seen_at and (now - session.last_seen_at).total_seconds() > STALE_SECONDS:
        return "stale"
    return "live"


def roster(scope: ProctorScope, *, group: str, page: int = 1, size: int = 12) -> dict:
    """Strona listy uczniów grupy – **już** zawężona do zakresu; kafle subskrybuje JS tej strony."""
    size = size if size in PAGE_SIZES else PAGE_SIZES[0]
    if not scope.allows_group(group):
        raise _not_found("Nie ma takiej grupy.")
    # Ta sama ważność, co ``active_consent`` (wersja, ustawienia etapu, nieodwołana zgoda opiekuna),
    # policzona jednym podzapytaniem dla całej strony.
    digests = [consent_digest(scope.config), consent_digest(scope.config, minor=True)]
    consented = ProctoringConsent.objects.filter(
        session=OuterRef("pk"), withdrawn_at__isnull=True, version=CONSENT_VERSION, text_sha256__in=digests
    ).filter(Q(guardian_record__isnull=True) | Q(guardian_record__withdrawn_at__isnull=True))
    rows = (
        scope.sessions()
        .filter(group=group)
        .select_related("participant__user", "proctor__user", "stage__edition__competition")
        .annotate(incident_count=Count("incidents", distinct=True), has_consent=Exists(consented))
        .order_by("participant__user__last_name", "participant__user__first_name", "pk")
    )
    total = rows.count()
    pages = max(1, (total + size - 1) // size)
    page = min(max(1, int(page)), pages)
    now = timezone.now()
    items = []
    for session in rows[(page - 1) * size : page * size]:
        items.append(
            {
                "id": session.pk,
                "identity": session.identity,
                "label": student_label(session.participant),
                "status": _status(session, now),
                "consent": session.has_consent,
                "screen": session.screen_live,
                "attendance": session.attendance,
                "incidents": session.incident_count,
                "unproctored": session.unproctored_at is not None,
                "unproctored_reason": session.unproctored_reason,
                "late": late_start_minutes(session),
                "alternative": session.alternative_status,
            }
        )
    return {"group": group, "page": page, "pages": pages, "size": size, "total": total, "items": items}


def late_start_minutes(session) -> int:
    """Ile minut po otwarciu **swojego** okna uczeń włączył nadzór – gdy więcej niż próg, inaczej 0.

    Późny start nie jest przewinieniem (awaria, spóźnienie), ale komisja ma go widzieć: uczeń mógł
    w tym czasie czytać zadania otwarte inną drogą. Próg: ``PROCTORING_LATE_START_MINUTES``.
    """
    if session.started_at is None:
        return 0
    opens, _closes = windows.effective_window(session.stage, session.participant)
    minutes = int((session.started_at - opens).total_seconds() // 60)
    threshold = max(0, int(getattr(settings, "PROCTORING_LATE_START_MINUTES", 15)))
    return minutes if minutes > threshold else 0


def proctor_token(scope: ProctorScope, group: str, *, request=None, now=None) -> dict:
    """Token nadzorującego do **jednego** pokoju grupy – opiekun dostaje wyłącznie swoją delegację."""
    now = now or timezone.now()
    if not configured():
        raise _unavailable()
    if not scope.allows_group(group):
        raise _not_found("Nie ma takiej grupy.")
    opens, closes = windows.proctor_window(scope.stage)
    if not (opens <= now < closes):
        raise DomainError(_("Pokój nadzoru jest teraz zamknięty."), "PROCTORING_ROOM_CLOSED", 409)
    room = scope.config.room_name(group)
    identity = proctor_identity(scope.stage, scope.user)
    token = livekit_api.access_token(identity=identity, name="", video=livekit_api.proctor_grants(room))
    _audit(
        scope.user,
        "proctoring.joined",
        scope.config,
        {"role": scope.kind, "group": group},
        request=request,
    )
    return {"url": _ws_url(), "token": token, "identity": identity, "group": group}


def send_message(scope: ProctorScope, session, kind: str, body: str, *, request=None) -> ProctoringMessage:
    """Wiadomość do ucznia: zapis i dziennik, potem ``SendData`` – awaria LiveKit niczego nie gubi
    (konsola ucznia odpytuje serwer co 20 s)."""
    if kind not in MessageKind.values:
        raise DomainError("Nieznany rodzaj wiadomości.", "PROCTORING_MESSAGE_KIND", 400)
    body = (body or "").strip()[:MESSAGE_MAX_LENGTH]
    if kind == MessageKind.TEXT and not body:
        raise DomainError(_("Wpisz treść wiadomości."), "PROCTORING_MESSAGE_EMPTY", 400)
    message = ProctoringMessage.objects.create(session=session, sender=scope.user, kind=kind, body=body)
    log_event(
        session,
        EventKind.MESSAGE,
        EventSource.PROCTOR,
        actor=scope.user,
        detail={"message": message.pk, "kind": kind},
    )
    _audit(scope.user, "proctoring.message_sent", session, {"kind": kind}, request=request)
    if configured():
        try:
            livekit_api.send_data(
                scope.config.room_name(session.group),
                session.identity,
                {"t": "msg", "id": message.pk, "k": kind, "b": body},
            )
        except livekit_api.LiveKitUnavailable, livekit_api.LiveKitError:
            logger.info("Nadzór: wiadomość %s bez kanału danych – dotrze odpytaniem.", message.pk)
        else:
            ProctoringMessage.objects.filter(pk=message.pk).update(via_data_channel=True)
            message.via_data_channel = True
    return message


def set_attendance(scope: ProctorScope, session, value: str, *, request=None) -> ProctoringSession:
    if value not in Attendance.values:
        raise DomainError("Nieznana wartość obecności.", "PROCTORING_ATTENDANCE", 400)
    session.attendance = value
    session.attendance_by = scope.user
    session.attendance_at = timezone.now()
    session.save(update_fields=["attendance", "attendance_by", "attendance_at"])
    log_event(session, EventKind.ATTENDANCE, EventSource.PROCTOR, actor=scope.user, detail={"value": value})
    _audit(scope.user, "proctoring.attendance", session, {"value": value}, request=request)
    return session


def create_incident(
    scope: ProctorScope, session, *, category: str, severity: str, note: str, occurred_at=None, request=None
) -> ProctoringIncident:
    if category not in IncidentCategory.values or severity not in IncidentSeverity.values:
        raise DomainError(_("Wybierz kategorię i wagę incydentu."), "PROCTORING_INCIDENT", 400)
    now = timezone.now()
    when = occurred_at or now
    # Czas zdarzenia nie może leżeć w przyszłości ani przed otwarciem pokoju – to byłby błąd pola,
    # a nie fakt z nadzoru.
    opens, _closes = windows.proctor_window(session.stage)
    if when > now + timedelta(minutes=1) or when < opens:
        when = now
    incident = ProctoringIncident.objects.create(
        session=session,
        reported_by=scope.user,
        occurred_at=when,
        category=category,
        severity=severity,
        note=(note or "").strip()[:2000],
    )
    log_event(
        session,
        EventKind.INCIDENT,
        EventSource.PROCTOR,
        actor=scope.user,
        detail={"incident": incident.pk, "category": category, "severity": severity},
        at=when,
    )
    _audit(
        scope.user,
        "proctoring.incident_created",
        session,
        {"category": category, "severity": severity},
        request=request,
    )
    return incident


# --- koordynator --------------------------------------------------------------------------------------

CONFIG_FIELDS = (
    "enabled",
    "require_screen_share",
    "require_microphone",
    "id_photo",
    "record",
    "on_unavailable",
    "instructions",
)


def proctorable(stage) -> bool:
    """Nadzór ma sens dla etapu online: rozwiązania pisemne, test – albo rozmowa w pokoju **LiveKit**
    (STAGE-LK-01: rozmowę na Jitsi nadzoru nie da się połączyć, bo pokój nie jest na naszym serwerze)."""
    from apps.competitions.models import StageFormat
    from apps.competitions.video import VideoProvider

    if stage.is_training:
        return False
    if stage.format == StageFormat.INTERVIEW:
        return stage.video_provider == VideoProvider.LIVEKIT
    return stage.format in (StageFormat.SUBMISSIONS, StageFormat.QUIZ)


def interview_ready(session, config) -> bool:
    """Rozmowa LiveKit w etapie z nadzorem: zgoda, sprawdzony sprzęt (i zdjęcie, gdy wymagane) albo
    zatwierdzona alternatywa. Kamery nie trzeba „włączać” osobno – pokój rozmowy **jest** obrazem."""
    if session is None:
        return False
    if session.alternative_approved:
        return True
    return (
        active_consent(session, config) is not None
        and session.check_passed_at is not None
        and photo_done(session, config)
    )


def save_config(stage, actor, data: dict, *, request=None) -> ProctoringConfig:
    if not proctorable(stage):
        raise DomainError(
            "Nadzór dotyczy wyłącznie etapów online (rozwiązania albo test).", "PROCTORING_STAGE", 400
        )
    config, _created = ProctoringConfig.objects.get_or_create(stage=stage)
    changed = []
    for name in CONFIG_FIELDS:
        if name in data and getattr(config, name) != data[name]:
            setattr(config, name, data[name])
            changed.append(name)
    config.updated_by = actor
    config.save()
    if changed:
        _audit(actor, "proctoring.config_updated", config, {"fields": changed}, request=request)
    return config


def assignment_candidates(stage) -> dict:
    """Konta, które wolno wskazać: koordynatorzy, aktywna komisja, opiekunowie drużyn (DEL-01)."""
    from apps.accounts.messaging import _role_filter
    from apps.accounts.models import CommitteeMember, CommitteeStatus, CompetitionRole, User

    competition = stage.edition.competition
    committee = CommitteeMember.objects.for_competition(competition).filter(status=CommitteeStatus.ACTIVE)
    base = User.objects.filter(is_active=True).exclude_anonymised().order_by("last_name", "first_name", "pk")
    result = {
        ProctorKind.COORDINATOR: list(base.filter(_role_filter(competition, CompetitionRole.COORDINATOR))),
        ProctorKind.COMMITTEE: list(base.filter(pk__in=committee.values("user_id"))),
        ProctorKind.LEADER: [],
    }
    if delegations.available() and hasattr(CompetitionRole, "TEAM_LEADER"):
        leaders = base.filter(_role_filter(competition, CompetitionRole.TEAM_LEADER))
        result[ProctorKind.LEADER] = [
            user for user in leaders if delegations.leader_delegation_id(user, competition, stage) is not None
        ]
    return result


def add_assignment(stage, actor, user, kind: str, *, request=None) -> ProctorAssignment:
    competition = stage.edition.competition
    delegation_id = None
    if kind == ProctorKind.COORDINATOR:
        allowed = is_coordinator(user, competition)
    elif kind == ProctorKind.COMMITTEE:
        allowed = committee_ok(user, competition)
    elif kind == ProctorKind.LEADER:
        delegation_id = delegations.leader_delegation_id(user, competition, stage)
        allowed = delegation_id is not None
    else:
        allowed = False
    if not allowed:
        raise DomainError(
            "Ta osoba nie ma w konkursie roli, której wymaga ten przydział.", "PROCTORING_ROLE", 400
        )
    assignment, created = ProctorAssignment.objects.update_or_create(
        stage=stage,
        user=user,
        defaults={"kind": kind, "delegation_id": delegation_id, "created_by": actor},
    )
    _audit(actor, "proctoring.assigned", assignment, {"kind": kind, "user": user.pk}, request=request)
    return assignment


def remove_assignment(stage, actor, pk, *, request=None) -> None:
    assignment = ProctorAssignment.objects.filter(stage=stage, pk=pk).first() if str(pk).isdigit() else None
    if assignment is None:
        raise _not_found("Nie ma takiego przydziału.")
    user = assignment.user
    groups = {f"a{assignment.pk}", *assignment.sessions.values_list("group", flat=True)}
    if assignment.delegation_id:
        groups.add(f"d{assignment.delegation_id}")
    moved = list(assignment.sessions.select_related("participant", "proctor"))
    assignment.delete()
    # Uczniowie odpiętego nadzorującego wracają do grupy bez przydziału (albo delegacji) – z pokoju
    # „a<przydział>” serwer ich wyprasza, konsola łączy się ponownie już do nowego pokoju.
    for session in moved:
        session.proctor = None
        old_group = session.group
        session.group = session_group(session)
        session.save(update_fields=["group"])
        if old_group != session.group and session.connected:
            _leave_room(stage, old_group, session)
    kick_proctor(stage, user, groups)
    _audit(actor, "proctoring.unassigned", stage, {"user": user.pk}, request=request)


def ensure_sessions(stage) -> int:
    """Sesje dla wszystkich zgłoszonych (bez dyskwalifikacji) – lista nadzoru przed startem etapu."""
    from apps.competitions.models import StageEntry, StageEntryStatus

    existing = set(ProctoringSession.objects.filter(stage=stage).values_list("participant_id", flat=True))
    entries = (
        StageEntry.objects.filter(stage=stage, participant__isnull=False)
        .exclude(status=StageEntryStatus.DISQUALIFIED)
        .exclude(participant_id__in=existing)
        .select_related("participant")
    )
    created = 0
    for entry in entries:
        session_for(stage, entry.participant)
        created += 1
    return created


def distribute(stage, actor, *, request=None) -> dict:
    """Rozdziela uczniów między nadzorujących po równo.

    Uczniowie delegacji idą najpierw do opiekunów **swojej** delegacji (gdy są przydzieleni), reszta –
    do komisji i koordynatorów z przydziałem, po kolei. Przydział istniejący nie jest ruszany
    (uczeń w trakcie etapu nie zmienia nadzorującego przez ponowne kliknięcie).
    """
    ensure_sessions(stage)
    assignments = list(ProctorAssignment.objects.filter(stage=stage).order_by("pk"))
    leaders: dict[int, list] = {}
    general = []
    for assignment in assignments:
        if assignment.kind == ProctorKind.LEADER and assignment.delegation_id:
            leaders.setdefault(assignment.delegation_id, []).append(assignment)
        else:
            general.append(assignment)
    load = {assignment.pk: assignment.sessions.count() for assignment in assignments}
    assigned = 0
    moved: list = []
    with transaction.atomic():
        unassigned = ProctoringSession.objects.filter(stage=stage, proctor__isnull=True).select_related(
            "participant"
        )
        for session in unassigned.order_by("pk"):
            pool = leaders.get(delegations.participant_delegation_id(session.participant) or 0) or general
            if not pool:
                continue
            target = min(pool, key=lambda item: (load[item.pk], item.pk))
            old_group = session.group
            session.proctor = target
            session.group = session_group(session)
            session.save(update_fields=["proctor", "group"])
            if old_group != session.group and session.connected:
                moved.append((old_group, session))
            load[target.pk] += 1
            assigned += 1
    # Po commicie: uczeń nadający do starego pokoju (np. ``m``) zostaje z niego wyproszony i wraca
    # z nowym tokenem do pokoju nadzorującego – inaczej koordynator widziałby go w złej grupie.
    for old_group, session in moved:
        _leave_room(stage, old_group, session)
    _audit(actor, "proctoring.distributed", stage, {"assigned": assigned}, request=request)
    return {"assigned": assigned}


def reassign(stage, session_pk, assignment_pk, actor, *, request=None) -> ProctoringSession:
    session = (
        ProctoringSession.objects.select_related("participant", "proctor")
        .filter(stage=stage, pk=session_pk)
        .first()
        if str(session_pk).isdigit()
        else None
    )
    if session is None:
        raise _not_found()
    assignment = None
    if str(assignment_pk).isdigit():
        assignment = ProctorAssignment.objects.filter(stage=stage, pk=assignment_pk).first()
        if assignment is None:
            raise _not_found("Nie ma takiego przydziału.")
        delegation_id = delegations.participant_delegation_id(session.participant)
        if assignment.kind == ProctorKind.LEADER and delegation_id != assignment.delegation_id:
            raise DomainError(
                "Opiekun drużyny może nadzorować wyłącznie uczniów swojej delegacji.",
                "PROCTORING_LEADER_SCOPE",
                400,
            )
    old_group = session.group
    session.proctor = assignment
    session.group = session_group(session)
    session.save(update_fields=["proctor", "group"])
    if old_group != session.group and session.connected:
        # Uczeń nadaje do pokoju poprzedniego nadzorującego – serwer go stamtąd wyprasza, a konsola
        # łączy się ponownie z nowym tokenem, czyli już do pokoju nowego nadzorującego.
        _leave_room(stage, old_group, session)
    _audit(
        actor,
        "proctoring.reassigned",
        session,
        {"assignment": getattr(assignment, "pk", None)},
        request=request,
    )
    return session


def _leave_room(stage, group: str, session) -> None:
    from apps.webinars import livekit

    if not configured():
        return
    config = config_for(stage)
    if config is None:
        return
    try:
        livekit.remove_participant(config.room_name(group), session.identity)
    except livekit.LiveKitUnavailable, livekit.LiveKitError:
        logger.info("Nadzór: uczeń sesji %s nie był w pokoju poprzedniej grupy.", session.pk)


def set_hold(session, actor, reason: str, *, request=None) -> ProctoringSession:
    reason = (reason or "").strip()[:200]
    session.hold_reason = reason
    session.save(update_fields=["hold_reason"])
    _audit(actor, "proctoring.hold_set" if reason else "proctoring.hold_released", session, request=request)
    return session


# --- raport i eksport ----------------------------------------------------------------------------------


def report_for(session) -> dict:
    """Raport ucznia dla komisji: zgoda, sprzęt, obecność, incydenty, wiadomości, dziennik, nagrania."""
    return {
        "session": session,
        "consent": session.consents.order_by("-given_at").first(),
        "incidents": list(session.incidents.select_related("reported_by").order_by("occurred_at", "pk")),
        # Nie ``messages`` – ta nazwa w kontekście szablonu należy do komunikatów Django.
        "student_messages": list(session.messages.select_related("sender").order_by("sent_at", "pk")),
        "events": list(session.events.select_related("actor").order_by("at", "pk")),
        "recordings": list(session.recordings.filter(purged_at__isnull=True).order_by("started_at", "pk")),
    }


INCIDENT_CSV_HEADER = (
    "kod uczestnika",
    "grupa",
    "czas zdarzenia",
    "kategoria",
    "waga",
    "notatka",
    "zgłosił",
    "obecność",
    "bez nadzoru",
    "późny start (min)",
    "alternatywa",
)

#: Powody pracy bez nadzoru – raport (komisja, w IQO po angielsku), CSV i siatka.
UNPROCTORED_LABELS = {
    UNPROCTORED_NOT_CONFIGURED: gettext_lazy("serwer nadzoru nieskonfigurowany"),
    UNPROCTORED_SERVER_UNREACHABLE: gettext_lazy("serwer nadzoru niedostępny"),
    UNPROCTORED_CONNECT_FAILURES: gettext_lazy("nieudane połączenia ucznia – serwer działał"),
}


def unproctored_label(session) -> str:
    if session.unproctored_at is None:
        return ""
    return str(UNPROCTORED_LABELS.get(session.unproctored_reason, "tak"))


def _csv_cell(value) -> str:
    """Komórka CSV bezpieczna dla arkusza – wzór zaczynający się od ``=+-@`` dostaje apostrof."""
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def incidents_csv(stage) -> str:
    from apps.competitions.jitsi_jwt import short_name

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(INCIDENT_CSV_HEADER)
    rows = (
        ProctoringIncident.objects.filter(session__stage=stage)
        .select_related("session__participant", "session__stage", "reported_by")
        .order_by("session__participant__public_code", "occurred_at", "pk")
    )

    def session_cells(session) -> tuple:
        return (
            session.get_attendance_display(),
            unproctored_label(session),
            late_start_minutes(session) or "",
            session.get_alternative_status_display() if session.alternative_status else "",
        )

    with_incidents = set()
    for incident in rows:
        session = incident.session
        with_incidents.add(session.pk)
        writer.writerow(
            [
                _csv_cell(cell)
                for cell in (
                    session.participant.public_code,
                    session.group,
                    timezone.localtime(incident.occurred_at).strftime("%Y-%m-%d %H:%M:%S"),
                    incident.get_category_display(),
                    incident.get_severity_display(),
                    incident.note,
                    short_name(incident.reported_by) if incident.reported_by else "",
                    *session_cells(session),
                )
            ]
        )
    # Uczniowie bez incydentu, ale z pracą bez nadzoru albo późnym startem – też dla komisji.
    others = (
        ProctoringSession.objects.filter(stage=stage)
        .filter(Q(started_at__isnull=False) | Q(unproctored_at__isnull=False))
        .exclude(pk__in=with_incidents)
        .select_related("participant", "stage")
        .order_by("participant__public_code", "pk")
    )
    for session in others:
        if not session.unproctored_at and not late_start_minutes(session):
            continue
        writer.writerow(
            [
                _csv_cell(cell)
                for cell in (
                    session.participant.public_code,
                    session.group,
                    "",
                    "",
                    "",
                    "",
                    "",
                    *session_cells(session),
                )
            ]
        )
    return buffer.getvalue()


def media_url(recording_or_session, *, user, competition, request=None) -> str:
    """Adres nagrania albo zdjęcia dokumentu na 15 min – koordynator albo komisja odwoławcza, z audytem."""
    if not can_review(user, competition):
        raise _not_found()
    if isinstance(recording_or_session, ProctoringRecording):
        recording = recording_or_session
        if recording.status != RecordingStatus.COMPLETE or recording.purged_at is not None:
            raise _not_found("Nie ma takiego nagrania.")
        key, content_type, action, target = (
            recording.storage_key,
            "video/webm",
            "proctoring.recording_viewed",
            recording.session,
        )
    else:
        key, content_type, action, target = (
            recording_or_session.id_photo_key,
            "image/jpeg",
            "proctoring.id_photo_viewed",
            recording_or_session,
        )
    if not key or not key.startswith(KEY_PREFIX):
        raise _not_found()
    _audit(user, action, target, request=request)
    return get_storage().presigned_get(
        key, ttl=MEDIA_URL_TTL_SECONDS, content_type=content_type, content_disposition="inline"
    )


# --- nagrania --------------------------------------------------------------------------------------


def recording_key(session, config, now) -> str:
    return (
        f"{KEY_PREFIX}{session.stage.edition.competition.slug}/{config.room_key}/"
        f"{session.identity}/{now:%Y%m%d-%H%M%S}.webm"
    )


def start_recording(session_pk: int, track_sid: str, room: str = "") -> ProctoringRecording | None:
    """Track Egress kamery – wyłącznie przy ``record=True`` i włączonym nadzorze (zadanie Celery).

    ``room`` – pokój rozmowy LiveKit (STAGE-LK-01); pusty = pokój nadzoru grupy ucznia."""
    session = (
        ProctoringSession.objects.select_related("stage__edition__competition").filter(pk=session_pk).first()
    )
    if session is None or not track_sid:
        return None
    config = config_for(session.stage)
    if config is None or not config.record or not configured():
        return None
    if ProctoringRecording.objects.filter(session=session, track_sid=track_sid).exists():
        return None
    now = timezone.now()
    key = recording_key(session, config, now)
    try:
        egress_id = livekit_api.start_track_recording(room or config.room_name(session.group), track_sid, key)
    except livekit_api.LiveKitUnavailable, livekit_api.LiveKitError:
        logger.warning("Nadzór: egress kamery sesji %s się nie uruchomił.", session.pk)
        log_event(session, EventKind.RECORDING, EventSource.SYSTEM, detail={"status": "failed_to_start"})
        return None
    if not egress_id:
        return None
    recording = ProctoringRecording.objects.create(
        session=session, egress_id=egress_id, track_sid=track_sid, storage_key=key, started_at=now
    )
    log_event(session, EventKind.RECORDING, EventSource.SYSTEM, detail={"status": "started"})
    return recording


# --- retencja ---------------------------------------------------------------------------------------


def purge_due_at(stage):
    """Kiedy nośniki etapu znikają: ``RETENTION_DAYS`` po późniejszej z dat publikacji wyników i końca
    okna reklamacji; bez publikacji – ``MAX_RETENTION_DAYS`` po końcu etapu (bezpiecznik)."""
    retention = timedelta(days=max(1, int(getattr(settings, "PROCTORING_RETENTION_DAYS", 30))))
    if stage.results_published_at is not None:
        base = max(stage.results_published_at, stage.appeal_window_closes_at)
        return base + retention
    maximum = timedelta(days=max(1, int(getattr(settings, "PROCTORING_MAX_RETENTION_DAYS", 180))))
    return stage.submission_deadline + maximum


def purge_session(session, *, now=None) -> dict:
    """Usuwa nośniki jednej sesji: nagrania (pliki), zdjęcie dokumentu, dziennik i wiadomości.

    Zostają: incydenty, obecność, zgody i decyzje o alternatywie – dokumentacja zawodów (odwołania
    po terminie, kontrola organu). Dziennik incydentu żyje w jego notatce, nie w zdarzeniach.
    """
    now = now or timezone.now()
    removed = {"recordings": 0, "photo": 0, "events": 0, "messages": 0}
    for recording in session.recordings.filter(purged_at__isnull=True):
        delete_quietly(recording.storage_key)
        ProctoringRecording.objects.filter(pk=recording.pk).update(purged_at=now)
        removed["recordings"] += 1
    if session.id_photo_key:
        delete_quietly(session.id_photo_key)
        removed["photo"] = 1
    removed["events"], _rows = session.events.exclude(kind=EventKind.INCIDENT).delete()
    removed["messages"], _rows = session.messages.all().delete()
    session.id_photo_key = ""
    session.check_result = {}
    # Uwaga ucznia do prośby o alternatywę bywa opisem sytuacji osobistej – znika z nośnikami;
    # zostaje sam powód z listy i decyzja koordynatora (dokumentacja zawodów).
    session.alternative_note = ""
    session.purged_at = now
    session.save(update_fields=["id_photo_key", "check_result", "alternative_note", "purged_at"])
    return removed


def purge_id_photos(now=None) -> int:
    """Zdjęcia dokumentu znikają **po etapie** (nie dopiero z resztą nośników): służą rozpoznaniu
    ucznia w czasie pisania, a nie odwołaniom. Wyjątek – sesja z wstrzymaniem usunięcia."""
    from apps.competitions.models import Stage

    now = now or timezone.now()
    removed = 0
    stages = Stage.objects.filter(proctoring_sessions__id_photo_at__isnull=False).distinct()
    for stage in stages.select_related("edition__competition"):
        _opens, closes = windows.proctor_window(stage)
        if closes > now:
            continue
        for session in ProctoringSession.objects.filter(stage=stage, hold_reason="").exclude(id_photo_key=""):
            delete_quietly(session.id_photo_key)
            ProctoringSession.objects.filter(pk=session.pk).update(id_photo_key="")
            log_event(session, EventKind.ID_PHOTO, EventSource.SYSTEM, detail={"status": "deleted"}, at=now)
            removed += 1
    return removed


def purge_expired(now=None) -> int:
    """Beat: sesje etapów po terminie retencji (bez ``hold``). Zwraca liczbę oczyszczonych sesji."""
    from apps.competitions.models import Stage

    now = now or timezone.now()
    purge_id_photos(now)
    stages = Stage.objects.filter(proctoring_sessions__purged_at__isnull=True).distinct()
    total = 0
    for stage in stages.select_related("edition__competition"):
        if purge_due_at(stage) > now:
            continue
        sessions = ProctoringSession.objects.filter(stage=stage, purged_at__isnull=True, hold_reason="")
        count = 0
        for session in sessions:
            purge_session(session, now=now)
            count += 1
        if count:
            from apps.tenancy.context import competition_context

            with competition_context(stage.edition.competition):
                _audit(None, "proctoring.purged", stage, {"sessions": count})
        total += count
    return total


# --- RODO: eksport i anonimizacja ---------------------------------------------------------------------


def _moment(value):
    return timezone.localtime(value).isoformat() if value else None


def export_section(participant) -> list[dict]:
    """Sekcja ``nadzor_zdalny`` eksportu danych konta – fakty o sesjach tej osoby, bez nośników.

    Pliki (nagrania, zdjęcie) nie jadą w paczce: to obraz z domu ucznia, przechowywany krótko
    w prywatnym buckecie; paczka mówi, **że** istnieją i do kiedy. Bez tożsamości nadzorujących
    (pracownicy organizatora – ta sama granica, co „Recenzent A/B”).
    """
    if participant is None:
        return []
    rows = ProctoringSession.objects.filter(participant=participant).select_related("stage__edition")
    result = []
    for session in rows.order_by("pk"):
        consent = session.consents.order_by("-given_at").first()
        result.append(
            {
                "etap": str(session.stage),
                "zgoda": {
                    "wersja": consent.version,
                    "wyrazona": _moment(consent.given_at),
                    "wycofana": _moment(consent.withdrawn_at),
                }
                if consent
                else None,
                "sprawdzenie_sprzetu": session.check_result or None,
                "nadzor_rozpoczety": _moment(session.started_at),
                "bez_nadzoru_od": _moment(session.unproctored_at),
                "obecnosc": session.get_attendance_display(),
                "alternatywa": session.get_alternative_status_display()
                if session.alternative_status
                else None,
                "zdjecie_dokumentu": bool(session.id_photo_key),
                "nagrania": session.recordings.filter(purged_at__isnull=True).count(),
                "usuniecie_nosnikow": _moment(purge_due_at(session.stage))
                if session.purged_at is None
                else None,
                "wiadomosci": [
                    {"czas": _moment(row.sent_at), "rodzaj": row.get_kind_display(), "tresc": row.body}
                    for row in session.messages.order_by("sent_at", "pk")
                ],
                "incydenty": [
                    {
                        "czas": _moment(row.occurred_at),
                        "kategoria": row.get_category_display(),
                        "waga": row.get_severity_display(),
                        "notatka": row.note,
                    }
                    for row in session.incidents.order_by("occurred_at", "pk")
                ],
                "zdarzenia": [
                    {"czas": _moment(row.at), "rodzaj": row.get_kind_display()}
                    for row in session.events.order_by("at", "pk")
                ],
            }
        )
    return result


def erase_for_participants(participants) -> int:
    """Anonimizacja konta: znikają nośniki (nagrania, zdjęcie, dziennik, wiadomości), zgody są wycofane.

    Incydenty i obecność zostają przy pseudonimowym profilu – jak oceny i prace (dokumentacja zawodów).
    """
    sessions = list(ProctoringSession.objects.filter(participant__in=participants))
    now = timezone.now()
    for session in sessions:
        # Uczeń, który usuwa konto w trakcie etapu, znika też z pokoju i z nagrywania.
        kick_student(session)
        purge_session(session, now=now)
    ProctoringConsent.objects.filter(session__in=sessions, withdrawn_at__isnull=True).update(withdrawn_at=now)
    return len(sessions)


# --- statystyki dla ekranu koordynatora ----------------------------------------------------------------


def stage_summary(stage, config=None) -> dict:
    """Liczniki ekranu koordynatora. ``distinct`` wszędzie: złączenie ze zgodami mnożyłoby wiersze."""
    rows = ProctoringSession.objects.filter(stage=stage)
    consent_filter = Q(consents__withdrawn_at__isnull=True, consents__version=CONSENT_VERSION)
    if config is not None:
        consent_filter &= Q(
            consents__text_sha256__in=[consent_digest(config), consent_digest(config, minor=True)]
        )
    summary = rows.aggregate(
        total=Count("pk", distinct=True),
        consented=Count("pk", filter=consent_filter, distinct=True),
        started=Count("pk", filter=Q(started_at__isnull=False), distinct=True),
        unproctored=Count("pk", filter=Q(unproctored_at__isnull=False), distinct=True),
        alternatives=Count("pk", filter=Q(alternative_status=AlternativeStatus.REQUESTED), distinct=True),
        unassigned=Count("pk", filter=Q(proctor__isnull=True), distinct=True),
        main_group=Count("pk", filter=Q(group=MAIN_GROUP), distinct=True),
    )
    summary["main_group_warning"] = summary["main_group"] > MAIN_GROUP_WARNING
    summary["main_group_limit"] = MAIN_GROUP_WARNING
    return summary
