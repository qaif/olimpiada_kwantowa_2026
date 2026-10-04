"""Sieć absolwentów: profil za zgodą, mentoring przez Wiadomości, zaproszenia i statystyki.

Zadanie ALUM-01 (04.10.2026, ``docs/tasks/ALUM-01.md``). Osobna aplikacja, a nie pola profilu
uczestnika, z trzech powodów:

- **inna podstawa prawna.** Profil uczestnika (``accounts.Participant``) istnieje, bo ktoś startuje
  w zawodach; profil absolwenta – bo ktoś **wyraził zgodę** (art. 6 ust. 1 lit. a RODO) na udział
  w sieci po zawodach. Wycofanie zgody kasuje profil absolwenta i nie może dotknąć niczego, co
  należy do dokumentacji zawodów – a to najprościej zagwarantować osobną tabelą,
- **inny czas życia.** Profil absolwenta żyje latami po ostatniej edycji, a retencja kont
  uczestników liczy się od deadline'u ostatniego etapu (``apps.accounts.retention``),
- **flaga konkursu** ``alumni`` – przy wyłączonej nic z tej aplikacji nie zadaje ani jednego
  zapytania na ścieżkach, które dziś istnieją.

Osiągnięć **nie przechowujemy** (``apps.alumni.achievements`` liczy je na żywo z ogłoszonych
wyników): kopia na profilu przeżyłaby cofnięcie publikacji, a „z opublikowanych wyników” ma
znaczyć dokładnie to, co jest dziś opublikowane.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.competitions.scoping import competition_scoped_manager

#: Wersja treści zgody na udział w sieci absolwentów. Zmiana brzmienia = nowa wersja; dowód
#: (``AlumniConsentEvent``) pamięta, pod którą wersją ktoś się zgodził.
ALUMNI_CONSENT_VERSION = "2026-10-04"

#: Nazwa przełącznika konkursu (``tenancy.FEATURE_DEFAULTS``).
ALUMNI_FLAG = "alumni"

MAX_BIO_LENGTH = 600
MAX_NOTE_LENGTH = 500
MAX_REASON_LENGTH = 500
MAX_INVITATION_BODY = 4000
#: Ile otwartych próśb i relacji (``REQUESTED`` + ``ACCEPTED``) może mieć jeden mentee naraz.
MAX_OPEN_REQUESTS_PER_MENTEE = 3
MIN_MENTOR_CAPACITY = 1
MAX_MENTOR_CAPACITY = 10
#: Próg k-anonimowości statystyk „gdzie są teraz” (§ 7).
K_ANONYMITY = 5


def enabled(competition) -> bool:
    """Czy konkurs ma włączoną sieć absolwentów. Bez zapytania – flaga jest polem wiersza konkursu."""
    return competition is not None and competition.has_feature(ALUMNI_FLAG)


def new_token() -> str:
    return secrets.token_urlsafe(16)


class Level(models.TextChoices):
    """Poziom osiągnięcia w jednej edycji. Kolejność wartości = kolejność rangi (:data:`LEVEL_RANK`)."""

    ANY = "ANY", _("uczestnik")
    QUALIFIED = "QUALIFIED", _("awans do kolejnego etapu")
    FINALIST = "FINALIST", _("finalista")
    LAUREATE = "LAUREATE", _("laureat")


LEVEL_RANK = {Level.ANY: 1, Level.QUALIFIED: 2, Level.FINALIST: 3, Level.LAUREATE: 4}


class Interest(models.TextChoices):
    """Zainteresowania i tematy mentoringu – lista **zamknięta**.

    Zamknięta, bo po niej się filtruje (katalog, zaproszenia) i liczy statystyki z progiem
    k-anonimowości: wolny tekst rozbiłby „fizyka kwantowa” na dziesięć pisowni, a każda z nich
    byłaby grupą poniżej progu.
    """

    QUANTUM_COMPUTING = "quantum_computing", _("obliczenia kwantowe")
    QUANTUM_PHYSICS = "quantum_physics", _("fizyka kwantowa")
    PHYSICS = "physics", _("fizyka")
    COMPUTER_SCIENCE = "computer_science", _("informatyka")
    MATHEMATICS = "mathematics", _("matematyka")
    ENGINEERING = "engineering", _("inżynieria")
    CHEMISTRY = "chemistry", _("chemia")
    RESEARCH = "research", _("badania naukowe")
    INDUSTRY = "industry", _("praca w przemyśle")
    EDUCATION = "education", _("edukacja i popularyzacja")


class AlumniSettings(models.Model):
    """Ustawienia sieci absolwentów jednego konkursu. Brak wiersza = wartości domyślne.

    Wzorzec ``ChatSettings``/``ForumSettings``: osobny model OneToOne zamiast kolumn konkursu, bo
    tabela konkursów ma złote testy migracji, a te ustawienia obchodzą tylko konkurs z flagą.
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="alumni_settings",
        verbose_name="konkurs",
    )
    eligibility = models.CharField(
        "kto może dołączyć", max_length=12, choices=Level.choices, default=Level.FINALIST
    )
    mentoring_enabled = models.BooleanField("mentoring włączony", default=False)
    public_wall = models.BooleanField("publiczna ściana absolwentów", default=False)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ustawienia sieci absolwentów"
        verbose_name_plural = "ustawienia sieci absolwentów"

    def __str__(self) -> str:
        return f"Absolwenci: {self.competition_id}"


class AlumniProfile(models.Model):
    """Profil absolwenta – istnieje **wtedy i tylko wtedy**, gdy jest ważna zgoda.

    Należy do ``Participant`` (profilu w **jednym** konkursie), a nie do konta: sieć absolwentów
    jest siecią konkursu, a zgoda dana organizatorowi A nie wystawia nikogo w sieci organizatora B.
    Wszystkie pola treści są opcjonalne – pokazujemy wyłącznie to, co ktoś sam wpisał.
    """

    participant = models.OneToOneField(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="alumni_profile",
        verbose_name="uczestnik",
    )
    token = models.CharField("identyfikator", max_length=32, unique=True, default=new_token)
    consent_version = models.CharField("wersja zgody", max_length=32)
    joined_at = models.DateTimeField("dołączył", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", default=timezone.now)

    show_full_name = models.BooleanField("pełne imię i nazwisko", default=False)
    university = models.CharField("uczelnia", max_length=120, blank=True)
    field_of_study = models.CharField("kierunek", max_length=120, blank=True)
    city = models.CharField("miasto", max_length=120, blank=True)
    #: ISO 3166-1 alpha-2 **małymi literami** – ta sama lista, co regiony krajowe
    #: (``apps.accounts.countries``). Pusty = nie podano (tu nie ma domyślnej Polski).
    country = models.CharField("kraj", max_length=2, blank=True)
    bio = models.TextField("o mnie", max_length=MAX_BIO_LENGTH, blank=True)
    interests = models.JSONField("zainteresowania", default=list, blank=True)
    linkedin_url = models.URLField("LinkedIn", max_length=300, blank=True)
    github_url = models.URLField("GitHub", max_length=300, blank=True)

    mentor_available = models.BooleanField("dostępny jako mentor", default=False)
    mentor_topics = models.JSONField("tematy mentoringu", default=list, blank=True)
    mentor_capacity = models.PositiveSmallIntegerField("ilu mentee naraz", default=2)

    listed = models.BooleanField("widoczny w katalogu", default=True)
    public = models.BooleanField("na publicznej ścianie", default=False)
    invitations = models.BooleanField("zaproszenia od organizatora", default=True)

    hidden_at = models.DateTimeField("ukryty przez koordynatora", null=True, blank=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="ukrył",
    )

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "profil absolwenta"
        verbose_name_plural = "profile absolwentów"
        ordering = ("-joined_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(mentor_capacity__gte=MIN_MENTOR_CAPACITY)
                & Q(mentor_capacity__lte=MAX_MENTOR_CAPACITY),
                name="alumni_profile_capacity_range",
            ),
        ]

    def __str__(self) -> str:
        return f"absolwent {self.participant_id}"

    @property
    def is_hidden(self) -> bool:
        return self.hidden_at is not None


class ConsentEventKind(models.TextChoices):
    GRANTED = "GRANTED", _("zgoda udzielona")
    WITHDRAWN = "WITHDRAWN", _("zgoda wycofana")


class AlumniConsentEvent(models.Model):
    """Dowód zgody (art. 7 ust. 1 RODO): kto, kiedy, pod jaką wersją treści – i kiedy ją wycofał.

    Zostaje także po wycofaniu: wiersz nie niesie danych poza pseudonimowym profilem uczestnika,
    a administrator musi umieć wykazać, że przetwarzał dane **na podstawie** zgody.
    """

    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="alumni_consent_events",
        verbose_name="uczestnik",
    )
    kind = models.CharField("zdarzenie", max_length=10, choices=ConsentEventKind.choices)
    version = models.CharField("wersja treści", max_length=32)
    created_at = models.DateTimeField("chwila", default=timezone.now)

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "zgoda absolwenta (zdarzenie)"
        verbose_name_plural = "zgody absolwentów (zdarzenia)"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.participant_id}: {self.kind} {self.version}"


class MentorshipStatus(models.TextChoices):
    REQUESTED = "REQUESTED", _("prośba czeka na mentora")
    ACCEPTED = "ACCEPTED", _("trwa")
    DECLINED = "DECLINED", _("odrzucona przez mentora")
    CANCELLED = "CANCELLED", _("wycofana przez uczestnika")
    ENDED = "ENDED", _("zakończona")


OPEN_STATUSES = (MentorshipStatus.REQUESTED, MentorshipStatus.ACCEPTED)


class EndReason(models.TextChoices):
    MENTOR = "MENTOR", _("zakończył mentor")
    MENTEE = "MENTEE", _("zakończył uczestnik")
    COORDINATOR = "COORDINATOR", _("zakończył organizator")
    WITHDRAWN = "WITHDRAWN", _("mentor wycofał zgodę")
    ACCOUNT_REMOVED = "ACCOUNT_REMOVED", _("konto usunięte")


class Channel(models.TextChoices):
    """Kanał rozmowy mentorskiej (``docs/tasks/ALUM-01.md`` § 5.2) – liczony, nie zapisywany."""

    PEER = "PEER", _("rozmowa według zasad Wiadomości")
    SUPERVISED = "SUPERVISED", _("każdą wiadomość akceptuje organizator")


class Mentorship(models.Model):
    """Prośba o mentoring i – po akceptacji – relacja mentor–mentee.

    Strony są profilami ``Participant``, a nie profilem absolwenta: wycofanie zgody kasuje profil
    absolwenta, a historia relacji (z kim, kiedy, kto zakończył) jest koordynatorowi potrzebna
    dalej – to on odpowiada za kontakt dorosłego z małoletnim na swojej platformie.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    mentor = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="mentorships_as_mentor",
        verbose_name="mentor",
    )
    mentee = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="mentorships_as_mentee",
        verbose_name="uczestnik",
    )
    status = models.CharField(
        "stan", max_length=10, choices=MentorshipStatus.choices, default=MentorshipStatus.REQUESTED
    )
    topic = models.CharField("temat", max_length=32, choices=Interest.choices, blank=True)
    note = models.TextField("notatka prośby", max_length=MAX_NOTE_LENGTH, blank=True)
    #: Rozmowa w Wiadomościach. ``SET_NULL``: rozmowę może zabrać kaskada konta, a relacja zostaje
    #: w historii nadzoru koordynatora.
    conversation = models.ForeignKey(
        "chat.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentorships",
        verbose_name="rozmowa",
    )
    #: Czy para rozmawiała już przed mentoringiem. Taka rozmowa po zakończeniu relacji wraca do
    #: zwykłych reguł czatu, a nie zamyka się do odczytu – nie mentoring ją założył.
    conversation_preexisting = models.BooleanField("rozmowa sprzed mentoringu", default=False)
    created_at = models.DateTimeField("prośba", default=timezone.now)
    responded_at = models.DateTimeField("odpowiedź mentora", null=True, blank=True)
    ended_at = models.DateTimeField("zakończona", null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zakończył",
    )
    end_reason = models.CharField("powód zakończenia", max_length=16, choices=EndReason.choices, blank=True)
    coordinator_note = models.CharField("notatka organizatora", max_length=MAX_REASON_LENGTH, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "mentoring"
        verbose_name_plural = "mentoring"
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["mentor", "mentee"],
                condition=Q(status__in=["REQUESTED", "ACCEPTED"]),
                name="alumni_one_open_mentorship_per_pair",
            ),
            models.CheckConstraint(condition=~Q(mentor=F("mentee")), name="alumni_mentor_not_mentee"),
        ]
        indexes = [
            models.Index(fields=["competition", "status", "-created_at"], name="alumni_mentorship_list_idx"),
        ]

    def __str__(self) -> str:
        return f"mentoring {self.mentor_id} → {self.mentee_id} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


class MentorshipFlag(models.Model):
    """Zgłoszenie problemu z relacją mentorską przez jedną ze stron – do koordynatora."""

    mentorship = models.ForeignKey(
        Mentorship, on_delete=models.CASCADE, related_name="flags", verbose_name="mentoring"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="zgłaszający",
    )
    reason = models.CharField("powód", max_length=MAX_REASON_LENGTH)
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    resolved_at = models.DateTimeField("rozpatrzone", null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="rozpatrzył",
    )

    objects = competition_scoped_manager("mentorship__competition")

    class Meta:
        verbose_name = "zgłoszenie mentoringu"
        verbose_name_plural = "zgłoszenia mentoringu"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"zgłoszenie mentoringu {self.mentorship_id}"


class InvitationKind(models.TextChoices):
    WORKSHOP = "WORKSHOP", _("warsztaty")
    WEBINAR = "WEBINAR", _("webinar")
    JURY = "JURY", _("jury")
    OTHER = "OTHER", _("inne")


class AlumniInvitation(models.Model):
    """Wysłane zaproszenie: co, do kogo (filtry) i do ilu osób. **Bez listy odbiorców** – liczba
    wystarcza do rozliczenia, a lista nazwisk byłaby drugim katalogiem absolwentów bez zgody na to."""

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    kind = models.CharField("rodzaj", max_length=10, choices=InvitationKind.choices)
    title = models.CharField("tytuł", max_length=150)
    body = models.TextField("treść", max_length=MAX_INVITATION_BODY)
    url = models.URLField("adres wydarzenia", max_length=500, blank=True)
    filters = models.JSONField("filtry", default=dict, blank=True)
    recipients = models.PositiveIntegerField("liczba odbiorców", default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="wysłał",
    )
    created_at = models.DateTimeField("wysłane", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "zaproszenie absolwentów"
        verbose_name_plural = "zaproszenia absolwentów"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.title
