"""Webinary w LiveKit (zadanie WEB-01): termin, odbiorcy, stan pokoju, obecność i nagrania.

Webinar **nie jest** pokojem Jitsi z polem „dostawca” (``docs/tasks/WEB-01.md`` § 1): ma termin
i czas trwania, grupę odbiorców z konkursu, własny interfejs pokoju na platformie, listę obecności
z webhooków i nagrania w naszym magazynie. Wspólne z Jitsi są tylko drobne zasady (nazwa w pokoju,
czyszczenie nazwy gościa, definicja członka komisji) – te bierzemy z modułów Jitsi wprost.

Model nie przechowuje **żadnego** tokenu ani sekretu: token LiveKit powstaje przy każdym wejściu
(``apps.webinars.services.join_token``) i trafia wyłącznie do odpowiedzi JSON ``no-store`` dla
przeglądarki tej osoby. Jedynym poświadczeniem w wierszu jest ``public_key`` linku dla gości –
i to tylko wtedy, gdy koordynator świadomie włączył ``public_link`` (domyślnie wyłączony). Klucza
transmisji YouTube **nie przechowujemy wcale** (``services.start_stream``).
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Flaga konkursu z ``apps.tenancy.models.FEATURE_DEFAULTS``. Czytana wyłącznie przez
#: ``apps.webinars.services.enabled`` – jedno miejsce reguły „czy ten konkurs ma webinary”.
FEATURE_FLAG = "webinars"

TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
#: Granice czasu trwania (minuty). Dolna, bo krótszy „webinar” to raczej test sprzętu; górna, bo
#: ośmiogodzinne spotkanie jest pomyłką w polu.
DURATION_MIN = 15
DURATION_MAX = 480

#: Bajty losowości klucza linku dla gości: 192 bity, jak klucze linków pokoi Jitsi.
PUBLIC_KEY_BYTES = 24


def new_public_key() -> str:
    """Klucz linku dla gości – identyfikator nie do zgadnięcia (``secrets``)."""
    return secrets.token_urlsafe(PUBLIC_KEY_BYTES)


def new_room_key() -> str:
    """Losowa końcówka nazwy pokoju LiveKit. Pełną nazwę składa :attr:`Webinar.room_name`.

    Losowa, a nie ``pk``: nazwa pokoju stoi w tokenach i w ścieżkach nagrań, a kolejne liczby
    pozwalałyby zgadywać pokoje innych konkursów na wspólnym serwerze (sama nazwa nie otwiera pokoju –
    potrzebny jest podpisany token – ale nie ma powodu ułatwiać wyliczania).
    """
    return secrets.token_hex(12)


class Audience(models.TextChoices):
    """Kto widzi webinar w panelu i dostaje zaproszenie. Koordynatorzy i prowadzący – zawsze."""

    COMPETITION = "competition", "wszyscy uczestnicy konkursu"
    EDITION = "edition", "uczestnicy bieżącej edycji"
    STAGE = "stage", "uczestnicy wybranego etapu"
    COMMITTEE = "committee", "komisja (recenzenci i komisja odwoławcza)"
    CAPTAINS = "captains", "kapitanowie drużyn bieżącej edycji"


class Webinar(models.Model):
    """Jeden webinar konkursu: termin, odbiorcy, stan pokoju po stronie platformy i LiveKit."""

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="webinars", verbose_name="konkurs"
    )
    title = models.CharField("tytuł", max_length=TITLE_MAX_LENGTH)
    description = models.TextField("opis", max_length=DESCRIPTION_MAX_LENGTH, blank=True)
    starts_at = models.DateTimeField("początek")
    duration_minutes = models.PositiveSmallIntegerField("czas trwania (min)", default=60)

    audience = models.CharField("odbiorcy", max_length=16, choices=Audience.choices, default=Audience.EDITION)
    #: Etap przy ``audience=stage``. ``SET_NULL``: skasowany etap nie może zabrać ze sobą webinaru
    #: (z nagraniami); webinar bez etapu nie ma wtedy odbiorców spośród uczestników – domyślnie
    #: zamknięte, nie „wszyscy”.
    stage = models.ForeignKey(
        "competitions.Stage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webinars",
        verbose_name="etap",
    )
    include_committee = models.BooleanField("także komisja", default=False)

    #: Link dla gości bez konta. Domyślnie wyłączony – webinar konkursu jest dla jego ludzi.
    public_link = models.BooleanField("link dla gości bez konta", default=False)
    public_key = models.CharField("klucz linku dla gości", max_length=64, unique=True, default=new_public_key)

    #: Czy koordynator może nagrywać (przycisk „Nagrywaj” w pokoju i na ekranie webinaru).
    record = models.BooleanField("nagrywanie", default=False)
    email_reminder = models.BooleanField("przypomnienie e-mailem", default=False)

    co_moderators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="webinars_co_moderated",
        verbose_name="współprowadzący",
    )

    room_key = models.CharField("końcówka nazwy pokoju", max_length=32, unique=True, default=new_room_key)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webinars_created",
        verbose_name="założył",
    )
    created_at = models.DateTimeField("założony", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", auto_now=True)
    #: „Rozpocznij” prowadzącego – od tej chwili (w oknie) wchodzą odbiorcy.
    started_at = models.DateTimeField("rozpoczęty", null=True, blank=True)
    #: „Zakończ” – pokój zamknięty dla wszystkich, nikt już nie wchodzi.
    ended_at = models.DateTimeField("zakończony", null=True, blank=True)
    cancelled_at = models.DateTimeField("odwołany", null=True, blank=True)
    announced_at = models.DateTimeField("zaproszenie wysłane", null=True, blank=True)
    reminder_sent_at = models.DateTimeField("przypomnienie wysłane", null=True, blank=True)
    #: Stan pokoju według **webhooków** LiveKit (``room_started`` / ``room_finished``).
    live_started_at = models.DateTimeField("pokój aktywny od", null=True, blank=True)
    live_finished_at = models.DateTimeField("pokój zamknięty", null=True, blank=True)
    #: Egress transmisji RTMP (YouTube), póki trwa. Sam adres z kluczem nie jest nigdzie zapisany.
    stream_egress_id = models.CharField("transmisja (egress)", max_length=64, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "webinar"
        verbose_name_plural = "webinary"
        ordering = ("starts_at", "id")
        indexes = [models.Index(fields=["competition", "starts_at"], name="webinars_comp_start_idx")]

    def __str__(self) -> str:
        return self.title

    @property
    def room_name(self) -> str:
        """Nazwa pokoju w LiveKit: prefiks platformy, konkurs i losowa końcówka.

        Konkurs w nazwie, bo jeden serwer LiveKit bywa wspólny dla kilku konkursów – administrator
        ma od razu widzieć, czyj to pokój, a webhook trafia po końcówce do właściwego wiersza.
        """
        return f"olimp-{self.competition.slug}-{self.room_key}"

    @property
    def ends_at(self):
        return self.starts_at + timedelta(minutes=self.duration_minutes)

    @property
    def is_live(self) -> bool:
        return self.live_started_at is not None and self.live_finished_at is None


class AttendeeRole(models.TextChoices):
    PRESENTER = "presenter", "prowadzący"
    VIEWER = "viewer", "widz"
    GUEST = "guest", "gość"


class WebinarAttendee(models.Model):
    """Osoba, która dostała token do pokoju, i jej obecność według webhooków (lista obecności).

    Wiersz powstaje przy wystawieniu tokenu (wiemy wtedy, **kto** to jest – konto i rola), a czasy
    wypełniają webhooki ``participant_joined`` / ``participant_left`` po ``identity``. Lista posłuży
    później do zaświadczeń o udziale; dziś koordynator widzi ją na ekranie webinaru.
    """

    webinar = models.ForeignKey(
        Webinar, on_delete=models.CASCADE, related_name="attendees", verbose_name="webinar"
    )
    identity = models.CharField("identyfikator w pokoju", max_length=64)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webinar_attendances",
        verbose_name="konto",
    )
    name = models.CharField("nazwa w pokoju", max_length=80, blank=True)
    role = models.CharField("rola", max_length=16, choices=AttendeeRole.choices, default=AttendeeRole.VIEWER)
    first_token_at = models.DateTimeField("pierwszy token", default=timezone.now)
    first_joined_at = models.DateTimeField("pierwsze wejście", null=True, blank=True)
    last_joined_at = models.DateTimeField("ostatnie wejście", null=True, blank=True)
    left_at = models.DateTimeField("wyjście", null=True, blank=True)
    seconds = models.PositiveIntegerField("czas obecności (s)", default=0)

    class Meta:
        verbose_name = "uczestnik webinaru"
        verbose_name_plural = "uczestnicy webinaru"
        ordering = ("webinar", "first_token_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["webinar", "identity"], name="webinars_attendee_unique_identity")
        ]

    def __str__(self) -> str:
        return f"{self.name or self.identity} @ {self.webinar_id}"

    @property
    def present(self) -> bool:
        return self.last_joined_at is not None and (
            self.left_at is None or self.left_at < self.last_joined_at
        )


class RecordingStatus(models.TextChoices):
    ACTIVE = "active", "nagrywa"
    COMPLETE = "complete", "gotowe"
    FAILED = "failed", "błąd"


class WebinarRecording(models.Model):
    """Nagranie z LiveKit Egress (MP4 w prywatnym buckecie). Odbiorcy widzą je po publikacji."""

    webinar = models.ForeignKey(
        Webinar, on_delete=models.CASCADE, related_name="recordings", verbose_name="webinar"
    )
    egress_id = models.CharField("egress", max_length=64, unique=True)
    status = models.CharField(
        "stan", max_length=16, choices=RecordingStatus.choices, default=RecordingStatus.ACTIVE
    )
    #: Klucz obiektu w buckecie nagrań. Ustala go platforma przy starcie (``filepath`` egress), a
    #: webhook ``egress_ended`` może go doprecyzować – zawsze w prefiksie tego webinaru.
    storage_key = models.CharField("plik", max_length=255)
    size = models.BigIntegerField("rozmiar (B)", null=True, blank=True)
    duration_seconds = models.PositiveIntegerField("długość (s)", null=True, blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="włączył",
    )
    started_at = models.DateTimeField("początek", default=timezone.now)
    ended_at = models.DateTimeField("koniec", null=True, blank=True)
    published = models.BooleanField("opublikowane", default=False)
    published_at = models.DateTimeField("opublikowane", null=True, blank=True)
    error = models.CharField("błąd", max_length=200, blank=True)

    class Meta:
        verbose_name = "nagranie webinaru"
        verbose_name_plural = "nagrania webinarów"
        ordering = ("-started_at", "-id")

    def __str__(self) -> str:
        return f"{self.webinar_id}: {self.storage_key}"


class WebinarWebhookEvent(models.Model):
    """Identyfikator już przetworzonego zdarzenia webhooka – zabezpieczenie przed powtórką.

    LiveKit ponawia dostarczenie, a przechwycone, poprawnie podpisane zdarzenie dałoby się wysłać
    jeszcze raz: unikalny ``event_id`` sprawia, że drugie dostarczenie nie zmienia niczego.
    Wiersze starsze niż tydzień sprząta zadanie beat (``tasks.remind_webinars``).
    """

    event_id = models.CharField("identyfikator zdarzenia", max_length=64, unique=True)
    event = models.CharField("zdarzenie", max_length=40)
    received_at = models.DateTimeField("odebrane", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "zdarzenie LiveKit"
        verbose_name_plural = "zdarzenia LiveKit"

    def __str__(self) -> str:
        return f"{self.event} {self.event_id}"


class WebinarNotificationSettings(models.Model):
    """Czy wysyłać zaproszenia i przypomnienia o webinarach. Brak wiersza znaczy **tak**.

    Po stronie konta, nie konkursu – ta sama reguła, co ``ChatNotificationSettings``: o skrzynce
    decyduje jej właściciel.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webinar_notification_settings",
        verbose_name="konto",
    )
    email_on_webinar = models.BooleanField("listy o webinarach", default=True)
    updated_at = models.DateTimeField("zmienione", auto_now=True)

    class Meta:
        verbose_name = "ustawienia powiadomień o webinarach"
        verbose_name_plural = "ustawienia powiadomień o webinarach"

    def __str__(self) -> str:
        return f"{self.user_id}: {'listy' if self.email_on_webinar else 'bez listów'}"
