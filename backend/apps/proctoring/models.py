"""Nadzór zdalny etapów online (zadanie PROC-01): ustawienia etapu, przydziały, sesje ucznia, dziennik.

Nadzór jest **przetwarzaniem wysokiego ryzyka** (obraz osób w większości niepełnoletnich, w ich
domach), więc model zbiera wyłącznie to, czego potrzebuje człowiek prowadzący nadzór i komisja
rozpatrująca odwołanie: stan zgody, wynik sprawdzenia sprzętu jako wartości logiczne, czasy
połączeń, wiadomości, incydenty z notatką i – tylko gdy koordynator świadomie włączy nagrywanie –
pliki nagrań w prywatnym buckecie. Żadnych odcisków sprzętu, żadnych analiz obrazu, żadnego śledzenia
kart przeglądarki (``docs/tasks/PROC-01.md`` § 0 i § 8).

Model **nie** przechowuje tokenów LiveKit (powstają przy każdym wejściu, jak w WEB-01) ani nazw
pokoi wprost – nazwę składa :meth:`ProctoringConfig.room_name` z losowej końcówki i grupy.

Nośniki z krótką retencją (zdjęcie dokumentu, nagrania, dziennik zdarzeń, wiadomości) kasuje beat
``tasks.purge_expired``; incydenty, obecność i zgody zostają jako dokumentacja zawodów.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Flaga konkursu z ``apps.tenancy.models.FEATURE_DEFAULTS``. Czytana wyłącznie przez
#: ``apps.proctoring.services.enabled``.
FEATURE_FLAG = "proctoring"

INSTRUCTIONS_MAX_LENGTH = 1000
MESSAGE_MAX_LENGTH = 500
NOTE_MAX_LENGTH = 2000
ALTERNATIVE_NOTE_MAX_LENGTH = 300

#: Grupa pokoju dla uczniów bez delegacji („main”).
MAIN_GROUP = "m"


def new_room_key() -> str:
    """Losowa końcówka nazw pokoi etapu – kolejne liczby pozwalałyby zgadywać cudze pokoje."""
    return secrets.token_hex(10)


class IdPhoto(models.TextChoices):
    OFF = "off", "nie zbieramy"
    OPTIONAL = "optional", "opcjonalne"
    REQUIRED = "required", "wymagane"


class OnUnavailable(models.TextChoices):
    """Co robi bramka etapu, gdy serwer LiveKit nie działa (``PROC-01`` § 6).

    Domyślnie ``block``: etap z nadzorem bez nadzoru jest wyjątkiem, który ma przyznać człowiek
    (alternatywa koordynatora), a nie przycisk w przeglądarce ucznia. ``allow`` to świadomy wybór
    organizatora dla etapów, w których awaria serwera nie może zatrzymać zawodów.
    """

    ALLOW = "allow", "pozwól pracować przy awarii serwera nadzoru (sesja ze znacznikiem)"
    BLOCK = "block", "zamknij treść etapu do decyzji koordynatora"


class ProctoringConfig(models.Model):
    """Nadzór jednego etapu. Brak wiersza albo ``enabled=False`` znaczy: etap bez nadzoru."""

    stage = models.OneToOneField(
        "competitions.Stage", on_delete=models.CASCADE, related_name="proctoring", verbose_name="etap"
    )
    enabled = models.BooleanField("nadzór włączony", default=False)
    require_screen_share = models.BooleanField("wymagane udostępnienie ekranu", default=False)
    require_microphone = models.BooleanField("wymagany mikrofon", default=False)
    id_photo = models.CharField(
        "zdjęcie dokumentu", max_length=16, choices=IdPhoto.choices, default=IdPhoto.OFF
    )
    #: Nagrywanie kamer. Domyślnie **wyłączone** – nagranie obrazu dziecka w jego pokoju jest
    #: najdalej idącą formą tego przetwarzania i ma być świadomą decyzją, a nie ustawieniem fabrycznym.
    record = models.BooleanField("nagrywanie kamer", default=False)
    on_unavailable = models.CharField(
        "gdy LiveKit nie działa", max_length=8, choices=OnUnavailable.choices, default=OnUnavailable.BLOCK
    )
    instructions = models.TextField(
        "dodatkowe instrukcje dla uczniów", max_length=INSTRUCTIONS_MAX_LENGTH, blank=True
    )
    room_key = models.CharField("końcówka nazw pokoi", max_length=32, unique=True, default=new_room_key)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField("zmienione", auto_now=True)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "nadzór etapu"
        verbose_name_plural = "nadzór etapów"

    def __str__(self) -> str:
        return f"nadzór etapu {self.stage_id}"

    def room_name(self, group: str) -> str:
        """``proc-<konkurs>-<końcówka>-<grupa>`` – grupa to ``m`` albo ``d<id delegacji>``."""
        return f"proc-{self.stage.edition.competition.slug}-{self.room_key}-{group}"


class ProctorKind(models.TextChoices):
    COORDINATOR = "coordinator", "koordynator"
    COMMITTEE = "committee", "komisja"
    LEADER = "leader", "opiekun drużyny"


class ProctorAssignment(models.Model):
    """„Ta osoba nadzoruje ten etap” – rola sprawdzana ponownie przy każdym tokenie i czynności."""

    stage = models.ForeignKey(
        "competitions.Stage",
        on_delete=models.CASCADE,
        related_name="proctor_assignments",
        verbose_name="etap",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proctor_assignments",
        verbose_name="konto",
    )
    kind = models.CharField("rola", max_length=16, choices=ProctorKind.choices)
    #: Delegacja opiekuna drużyny (migawka z ``apps.proctoring.delegations``). Liczba, a nie klucz
    #: obcy: model delegacji należy do DEL-01 i aplikacja działa także bez niego.
    delegation_id = models.PositiveBigIntegerField("delegacja", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("przydzielony", default=timezone.now)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "nadzorujący"
        verbose_name_plural = "nadzorujący"
        ordering = ("stage", "kind", "id")
        constraints = [
            models.UniqueConstraint(fields=["stage", "user"], name="proctoring_assignment_unique_user"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → etap {self.stage_id} ({self.kind})"


class Attendance(models.TextChoices):
    UNKNOWN = "unknown", "nie odnotowano"
    PRESENT = "present", "obecny"
    ABSENT = "absent", "nieobecny"


class AlternativeStatus(models.TextChoices):
    NONE = "", "brak"
    REQUESTED = "requested", "prośba"
    APPROVED = "approved", "zatwierdzona"
    REJECTED = "rejected", "odrzucona"


class AlternativeReason(models.TextChoices):
    """Powód prośby o alternatywę – z listy, żeby uczeń nie musiał opisywać zdrowia (art. 9 RODO)."""

    NO_CAMERA = "no_camera", "brak kamery"
    CAMERA_BROKEN = "camera_broken", "kamera nie działa"
    NO_BROWSER = "no_browser", "przeglądarka nie obsługuje nadzoru"
    ACCESSIBILITY = "accessibility", "potrzeby dostępności"
    OTHER = "other", "inny powód"


class ProctoringSession(models.Model):
    """Stan jednego ucznia w nadzorze jednego etapu."""

    stage = models.ForeignKey(
        "competitions.Stage",
        on_delete=models.CASCADE,
        related_name="proctoring_sessions",
        verbose_name="etap",
    )
    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="proctoring_sessions",
        verbose_name="uczestnik",
    )
    #: ``identity`` w LiveKit – pseudonim HMAC (``p-…``) osobny na etap; po nim webhook trafia do sesji.
    #: Nie jest sekretem (widzą go nadzorujący i serwer LiveKit), nie zdradza ``pk`` ani adresu.
    identity = models.CharField("identyfikator w pokoju", max_length=32, db_index=True)
    #: Pseudonim **konta** (``u-…``, WEB-01) – tak uczeń nazywa się w pokoju rozmowy LiveKit
    #: (STAGE-LK-01). Zapisany, żeby webhook pokoju rozmowy trafiał do sesji jednym zapytaniem,
    #: a nie liczeniem HMAC dla każdego zapisu etapu.
    account_identity = models.CharField("pseudonim konta", max_length=32, blank=True, db_index=True)
    #: Grupa pokoju – ``m`` albo ``d<id delegacji>``; liczona przy każdym tokenie (migawka do listy).
    group = models.CharField("grupa", max_length=24, default=MAIN_GROUP)
    proctor = models.ForeignKey(
        ProctorAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
        verbose_name="nadzorujący",
    )
    #: Wynik sprawdzenia sprzętu – **tylko wartości logiczne** i rodzina przeglądarki.
    check_result = models.JSONField("wynik sprawdzenia", default=dict, blank=True)
    check_passed_at = models.DateTimeField("sprzęt sprawdzony", null=True, blank=True)
    id_photo_key = models.CharField("zdjęcie dokumentu (klucz)", max_length=255, blank=True)
    id_photo_at = models.DateTimeField("zdjęcie dokumentu", null=True, blank=True)
    #: Pierwsze **potwierdzone przez serwer LiveKit** nadawanie kamery – od tej chwili etap jest otwarty.
    started_at = models.DateTimeField("nadzór rozpoczęty", null=True, blank=True)
    #: Praca bez nadzoru przy ``on_unavailable=allow`` (LiveKit nie działał) – znacznik dla komisji.
    unproctored_at = models.DateTimeField("bez nadzoru od", null=True, blank=True)
    #: Dlaczego wolno było pracować bez nadzoru: ``not_configured`` / ``server_unreachable`` (serwer
    #: niedostępny dla **platformy**) / ``connect_failures`` (serwer działał, połączenia ucznia nie).
    unproctored_reason = models.CharField("powód pracy bez nadzoru", max_length=24, blank=True)
    connected = models.BooleanField("połączony", default=False)
    camera_live = models.BooleanField("kamera nadaje", default=False)
    screen_live = models.BooleanField("ekran udostępniony", default=False)
    last_seen_at = models.DateTimeField("ostatni sygnał", null=True, blank=True)
    attendance = models.CharField(
        "obecność", max_length=8, choices=Attendance.choices, default=Attendance.UNKNOWN
    )
    attendance_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    attendance_at = models.DateTimeField("obecność odnotowana", null=True, blank=True)
    alternative_status = models.CharField(
        "alternatywa",
        max_length=12,
        choices=AlternativeStatus.choices,
        default=AlternativeStatus.NONE,
        blank=True,
    )
    alternative_reason = models.CharField(
        "powód", max_length=16, choices=AlternativeReason.choices, blank=True
    )
    alternative_note = models.CharField("uwaga ucznia", max_length=ALTERNATIVE_NOTE_MAX_LENGTH, blank=True)
    alternative_requested_at = models.DateTimeField("prośba z", null=True, blank=True)
    #: Ustalenie koordynatora („nadzór telefoniczny o 9:00”) – widzi je uczeń.
    alternative_decision = models.CharField("ustalenie", max_length=ALTERNATIVE_NOTE_MAX_LENGTH, blank=True)
    alternative_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    alternative_decided_at = models.DateTimeField("decyzja z", null=True, blank=True)
    #: Komisja wstrzymała usunięcie nośników (sprawa w toku). Pusty = retencja zwykła.
    hold_reason = models.CharField("wstrzymanie usunięcia", max_length=200, blank=True)
    purged_at = models.DateTimeField("nośniki usunięte", null=True, blank=True)
    created_at = models.DateTimeField("założona", default=timezone.now)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "sesja nadzoru"
        verbose_name_plural = "sesje nadzoru"
        ordering = ("stage", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["stage", "participant"], name="proctoring_session_unique_participant"
            ),
        ]
        indexes = [models.Index(fields=["stage", "group"], name="proctoring_session_group_idx")]

    def __str__(self) -> str:
        return f"nadzór {self.participant_id} @ {self.stage_id}"

    @property
    def alternative_approved(self) -> bool:
        return self.alternative_status == AlternativeStatus.APPROVED


class ProctoringConsent(models.Model):
    """Dowód zgody na nadzór: wersja i skrót treści, czas, adres, zgoda opiekuna niepełnoletniego."""

    session = models.ForeignKey(ProctoringSession, on_delete=models.CASCADE, related_name="consents")
    version = models.CharField("wersja", max_length=64)
    #: Skrót wersji, oświadczenia **i ustawień etapu** (nagrywanie, mikrofon, ekran, zdjęcie) – zmiana
    #: ustawień po zgodzie unieważnia ją (``services.active_consent``).
    text_sha256 = models.CharField("skrót treści", max_length=64)
    terms = models.JSONField("ustawienia etapu w chwili zgody", default=dict, blank=True)
    #: Niepełnoletni zaznaczył oświadczenie, że opiekun zna informację o nadzorze i się zgadza.
    guardian_statement = models.BooleanField("oświadczenie o zgodzie opiekuna na nadzór", default=False)
    given_at = models.DateTimeField("wyrażona", default=timezone.now)
    ip = models.GenericIPAddressField("adres IP", null=True, blank=True)
    #: Wpis zgody opiekuna złożonej online (``apps.accounts.guardian``), na którym oparto zgodę
    #: niepełnoletniego. ``SET_NULL`` – dowód tej zgody nie może zniknąć z cudzym wierszem.
    guardian_record = models.ForeignKey(
        "accounts.ConsentRecord", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    withdrawn_at = models.DateTimeField("wycofana", null=True, blank=True)

    class Meta:
        verbose_name = "zgoda na nadzór"
        verbose_name_plural = "zgody na nadzór"
        ordering = ("-given_at", "-id")

    def __str__(self) -> str:
        return f"zgoda {self.version} ({self.session_id})"


class EventKind(models.TextChoices):
    CHECK_PASSED = "check_passed", "sprzęt sprawdzony"
    CHECK_FAILED = "check_failed", "sprawdzenie nieudane"
    CONSENT_GIVEN = "consent_given", "zgoda wyrażona"
    CONSENT_WITHDRAWN = "consent_withdrawn", "zgoda wycofana"
    ID_PHOTO = "id_photo", "zdjęcie dokumentu"
    STARTED = "started", "nadawanie potwierdzone"
    UNPROCTORED = "unproctored", "praca bez nadzoru"
    CONNECTED = "connected", "połączenie"
    DISCONNECTED = "disconnected", "rozłączenie"
    CAMERA_ON = "camera_on", "kamera nadaje"
    CAMERA_OFF = "camera_off", "kamera przestała nadawać"
    SCREEN_ON = "screen_on", "ekran udostępniony"
    SCREEN_OFF = "screen_off", "udostępnianie ekranu zakończone"
    STREAM_DROPPED = "stream_dropped", "zerwany strumień (zgłoszenie przeglądarki)"
    CONNECT_FAILED = "connect_failed", "nieudane połączenie (zgłoszenie przeglądarki)"
    RECONNECTED = "reconnected", "ponowne połączenie (zgłoszenie przeglądarki)"
    LIVEKIT_UNAVAILABLE = "livekit_unavailable", "serwer nadzoru niedostępny"
    MESSAGE = "message", "wiadomość nadzorującego"
    MESSAGE_SEEN = "message_seen", "wiadomość potwierdzona"
    ATTENDANCE = "attendance", "obecność"
    INCIDENT = "incident", "incydent"
    ALTERNATIVE = "alternative", "alternatywa"
    RECORDING = "recording", "nagranie"


class EventSource(models.TextChoices):
    CLIENT = "client", "przeglądarka ucznia"
    WEBHOOK = "webhook", "serwer LiveKit"
    PROCTOR = "proctor", "nadzorujący"
    SYSTEM = "system", "platforma"


class ProctoringEvent(models.Model):
    """Dziennik sesji – zdarzenia techniczne i czynności nadzorujących, bez treści wiadomości."""

    session = models.ForeignKey(ProctoringSession, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField("rodzaj", max_length=24, choices=EventKind.choices)
    source = models.CharField("źródło", max_length=8, choices=EventSource.choices)
    at = models.DateTimeField("czas", default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    detail = models.JSONField("szczegóły", default=dict, blank=True)

    class Meta:
        verbose_name = "zdarzenie nadzoru"
        verbose_name_plural = "zdarzenia nadzoru"
        ordering = ("at", "id")
        indexes = [models.Index(fields=["session", "at"], name="proctoring_event_session_idx")]

    def __str__(self) -> str:
        return f"{self.kind} @ {self.at:%H:%M:%S}"


class MessageKind(models.TextChoices):
    TEXT = "text", "wiadomość"
    SHOW_ROOM = "show_room", "prośba: pokaż pokój"
    SHOW_ID = "show_id", "prośba: pokaż dokument"


class ProctoringMessage(models.Model):
    session = models.ForeignKey(ProctoringSession, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    kind = models.CharField("rodzaj", max_length=12, choices=MessageKind.choices, default=MessageKind.TEXT)
    body = models.CharField("treść", max_length=MESSAGE_MAX_LENGTH, blank=True)
    sent_at = models.DateTimeField("wysłana", default=timezone.now)
    via_data_channel = models.BooleanField("kanałem danych", default=False)
    seen_at = models.DateTimeField("potwierdzona", null=True, blank=True)

    class Meta:
        verbose_name = "wiadomość nadzoru"
        verbose_name_plural = "wiadomości nadzoru"
        ordering = ("sent_at", "id")

    def __str__(self) -> str:
        return f"{self.kind} → {self.session_id}"


class IncidentCategory(models.TextChoices):
    ABSENT = "absent", "uczeń poza kadrem"
    OTHER_PERSON = "other_person", "inna osoba w kadrze"
    DEVICE = "device", "inne urządzenie"
    COMMUNICATION = "communication", "rozmowa lub komunikacja"
    MATERIALS = "materials", "niedozwolone materiały"
    SCREEN = "screen", "ekran"
    TECHNICAL = "technical", "problem techniczny"
    OTHER = "other", "inne"


class IncidentSeverity(models.TextChoices):
    INFO = "info", "informacja"
    WARNING = "warning", "ostrzeżenie"
    SERIOUS = "serious", "poważny"


class ProctoringIncident(models.Model):
    """Incydent zgłoszony przez człowieka – podstawa raportu dla komisji. Bez oceny automatycznej."""

    session = models.ForeignKey(ProctoringSession, on_delete=models.CASCADE, related_name="incidents")
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    occurred_at = models.DateTimeField("czas zdarzenia", default=timezone.now)
    created_at = models.DateTimeField("zgłoszony", default=timezone.now)
    category = models.CharField("kategoria", max_length=16, choices=IncidentCategory.choices)
    severity = models.CharField(
        "waga", max_length=8, choices=IncidentSeverity.choices, default=IncidentSeverity.WARNING
    )
    note = models.TextField("notatka", max_length=NOTE_MAX_LENGTH, blank=True)

    class Meta:
        verbose_name = "incydent nadzoru"
        verbose_name_plural = "incydenty nadzoru"
        ordering = ("occurred_at", "id")

    def __str__(self) -> str:
        return f"{self.category} ({self.session_id})"


class RecordingStatus(models.TextChoices):
    ACTIVE = "active", "nagrywa"
    COMPLETE = "complete", "gotowe"
    FAILED = "failed", "błąd"


class ProctoringRecording(models.Model):
    """Nagranie jednej ścieżki kamery (Track Egress, bez transkodowania) w prywatnym buckecie."""

    session = models.ForeignKey(ProctoringSession, on_delete=models.CASCADE, related_name="recordings")
    egress_id = models.CharField("egress", max_length=64, unique=True)
    track_sid = models.CharField("ścieżka", max_length=64)
    status = models.CharField(
        "stan", max_length=10, choices=RecordingStatus.choices, default=RecordingStatus.ACTIVE
    )
    storage_key = models.CharField("plik", max_length=255)
    size = models.BigIntegerField("rozmiar (B)", null=True, blank=True)
    duration_seconds = models.PositiveIntegerField("długość (s)", null=True, blank=True)
    started_at = models.DateTimeField("początek", default=timezone.now)
    ended_at = models.DateTimeField("koniec", null=True, blank=True)
    error = models.CharField("błąd", max_length=200, blank=True)
    purged_at = models.DateTimeField("usunięte", null=True, blank=True)

    class Meta:
        verbose_name = "nagranie nadzoru"
        verbose_name_plural = "nagrania nadzoru"
        ordering = ("started_at", "id")

    def __str__(self) -> str:
        return f"{self.session_id}: {self.storage_key}"


class RoomBlockKind(models.TextChoices):
    REMOVED = "removed", "usunięty z pokoju"
    MUTED = "muted", "bez głosu"


class InterviewRoomBlock(models.Model):
    """Decyzja moderatora pokoju rozmowy LiveKit (STAGE-LK-01), która ma **przetrwać ponowne wejście**.

    „Usuń” i „odbierz głos” to polecenia dla trwającego połączenia; uczeń, który odświeży stronę,
    dostałby nowy token z pełnymi uprawnieniami i decyzja by się „odkręciła”. Wiersz tutaj sprawia,
    że token na **ten termin** jest odmówiony (usunięty) albo wydany bez nadawania (bez głosu), dopóki
    moderator nie wpuści ponownie / nie odda głosu. Termin się kończy – wiersz przestaje mieć znaczenie
    (token i tak nie powstanie poza oknem), a kasuje go kaskada razem z terminem.
    """

    slot = models.ForeignKey(
        "competitions.InterviewSlot",
        on_delete=models.CASCADE,
        related_name="room_blocks",
        verbose_name="termin",
    )
    #: Pseudonim konta (``u-…``) – ten sam, którym osoba jest w pokoju.
    identity = models.CharField("identyfikator w pokoju", max_length=32)
    kind = models.CharField("decyzja", max_length=8, choices=RoomBlockKind.choices)
    #: Podpis dla moderatora („Imię N.”), ustalany przy decyzji z zapisów **tego** terminu.
    label = models.CharField("osoba", max_length=80, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("od", default=timezone.now)

    class Meta:
        verbose_name = "decyzja moderatora rozmowy"
        verbose_name_plural = "decyzje moderatorów rozmów"
        constraints = [
            models.UniqueConstraint(fields=["slot", "identity"], name="proctoring_room_block_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.identity} @ {self.slot_id}"
