"""Tłumaczenia zadań przez delegacje krajowe (docs/tasks/TR-01.md § 1).

Siedem modeli i ich granice:

- ``TranslationWindow`` – kiedy delegacje tłumaczą zadania etapu i czy każda osobno, czy wspólnie
  z innymi delegacjami tego samego języka. Okno gaśnie **najpóźniej w chwili otwarcia etapu**:
  od tej sekundy treść zadania i tak jest jawna, a edytor otwarty dłużej znaczyłby tłumaczenie
  poprawiane w trakcie zawodów,
- ``ProblemSource`` i ``SourceRevision`` – wersja oficjalna zadania (tekst Markdown obok PDF-u
  z ``Problem.statement_pdf``) z numerem wersji. Numer jest tym, po czym tłumaczenie wie, że stało
  się nieaktualne – porównanie liczb, a nie treści, bo treścią bywa plik PDF,
- ``DelegationLanguage`` – na jakie języki tłumaczy delegacja (jeden albo dwa),
- ``Translation`` i ``TranslationRevision`` – roboczy stan tłumaczenia i migawki z każdego „Wyślij do
  akceptacji”. Uczeń i eksport czytają **migawkę zatwierdzoną**, nigdy roboczy tekst: opiekun może
  otworzyć zatwierdzone tłumaczenie do poprawek, a uczniowie nie zobaczą przy tym półproduktu,
- ``StudentLanguage`` – nadpisanie języka jednego ucznia (domyślny jest język 1 delegacji).

Zakres konkursu: każdy model dochodzi do konkursu ścieżką kluczy obcych (``competition_scoped_manager``)
– wybór obiektu w widoku zawsze idzie przez ``for_competition``, więc identyfikator z innego konkursu
daje 404 z zawężenia, a nie z warunku.
"""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager
from apps.competitions.storage import private_media_storage

from .languages import CODE_MAX_LENGTH, english_name, label

#: Górna granica tekstu tłumaczenia i źródła. Zadanie olimpijskie to dwie–cztery strony; sto
#: tysięcy znaków mieści z zapasem najdłuższe, a odcina wklejenie przypadkowego pliku.
BODY_MAX_LENGTH = 100_000


class SharingMode(models.TextChoices):
    """Czy delegacje jednego języka mają osobne tłumaczenia, czy jedno wspólne."""

    SEPARATE = "SEPARATE", "osobne – każda delegacja tłumaczy sama"
    SHARED = "SHARED", "wspólne – jedno tłumaczenie na język"


class TranslationWindow(models.Model):
    """Okno tłumaczeń etapu – ustawia koordynator, czyta każda bramka poufności."""

    stage = models.OneToOneField(
        "competitions.Stage", on_delete=models.CASCADE, related_name="translation_window", verbose_name="etap"
    )
    opens_at = models.DateTimeField("otwarcie okna")
    closes_at = models.DateTimeField("zamknięcie okna")
    sharing_mode = models.CharField(
        "tłumaczenia", max_length=16, choices=SharingMode.choices, default=SharingMode.SEPARATE
    )
    updated_at = models.DateTimeField("zmienione", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "okno tłumaczeń"
        verbose_name_plural = "okna tłumaczeń"
        constraints = [
            models.CheckConstraint(
                condition=Q(opens_at__lt=F("closes_at")), name="problem_translations_window_ordered"
            ),
        ]

    def __str__(self) -> str:
        return f"okno tłumaczeń {self.stage_id}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            errors["closes_at"] = "Zamknięcie okna musi być po jego otwarciu."
        # Reguła z polecenia: okno zamyka się **przed** otwarciem etapu (najpóźniej w tej samej
        # chwili). Model pilnuje zapisu, a ``effective_closes_at`` – etapu przesuniętego później.
        if self.closes_at and self.stage_id and self.closes_at > self.stage.opens_at:
            errors["closes_at"] = "Okno tłumaczeń musi się zamknąć najpóźniej w chwili otwarcia etapu."
        if errors:
            raise ValidationError(errors)

    @property
    def effective_closes_at(self):
        """Faktyczne zamknięcie: wcześniejsza z dat – okna i otwarcia etapu.

        Etap przesunięty na wcześniej po ustawieniu okna nie może zostawić edytora otwartego w trakcie
        zawodów; minimum liczone przy odczycie nie wymaga, żeby ekran etapu pamiętał o oknie.
        """
        return min(self.closes_at, self.stage.opens_at)

    def is_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.opens_at <= now < self.effective_closes_at

    def state(self, now=None) -> str:
        """``upcoming`` / ``open`` / ``closed`` – do odznak na ekranach."""
        now = now or timezone.now()
        if now < self.opens_at:
            return "upcoming"
        if now < self.effective_closes_at:
            return "open"
        return "closed"


def source_fingerprint(problem, body_md: str) -> str:
    """Odcisk wersji oficjalnej: tytuły i **nazwy** plików PDF (wersja główna i angielska) oraz tekst.

    Obie wersje językowe, bo w konkursie anglojęzycznym uczeń dostaje ``statement_pdf_en``
    (``Problem.statement_file``) – erratum wgrane tylko do pliku angielskiego jest zmianą tego, co
    tłumacze przekładają. Nazwa pliku, a nie jego treść: każde wgranie nowego PDF-u dostaje w storage
    nową nazwę, a czytanie całego pliku przy każdym zapisie zadania kosztowałoby pobranie go z MinIO.
    """
    raw = "\x00".join(
        [
            problem.title or "",
            problem.statement_pdf.name or "",
            body_md or "",
            problem.title_en or "",
            problem.statement_pdf_en.name or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProblemSource(models.Model):
    """Wersja oficjalna zadania jako tekst (Markdown + LaTeX) i numer bieżącej wersji."""

    problem = models.OneToOneField(
        "competitions.Problem", on_delete=models.CASCADE, related_name="translation_source"
    )
    body_md = models.TextField("treść oficjalna (Markdown)", blank=True, max_length=BODY_MAX_LENGTH)
    version = models.PositiveIntegerField("wersja", default=1)
    fingerprint = models.CharField("odcisk wersji", max_length=64)
    updated_at = models.DateTimeField("zmieniona", default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = competition_scoped_manager("problem__stage__edition__competition")

    class Meta:
        verbose_name = "wersja oficjalna zadania"
        verbose_name_plural = "wersje oficjalne zadań"

    def __str__(self) -> str:
        return f"źródło zadania {self.problem_id} v{self.version}"


class SourceRevision(models.Model):
    """Migawka wersji oficjalnej – z niej tłumacz widzi, **co** się zmieniło od jego wersji."""

    source = models.ForeignKey(ProblemSource, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField("wersja")
    title = models.CharField("tytuł", max_length=300)
    body_md = models.TextField("treść (Markdown)", blank=True)
    pdf_name = models.CharField("plik PDF", max_length=300, blank=True)
    title_en = models.CharField("tytuł (EN)", max_length=300, blank=True)
    pdf_en_name = models.CharField("plik PDF (EN)", max_length=300, blank=True)
    created_at = models.DateTimeField("utworzona", default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = competition_scoped_manager("source__problem__stage__edition__competition")

    class Meta:
        verbose_name = "wersja źródła"
        verbose_name_plural = "wersje źródła"
        ordering = ("source", "version")
        constraints = [
            models.UniqueConstraint(fields=["source", "version"], name="problem_translations_source_version"),
        ]

    def __str__(self) -> str:
        return f"źródło {self.source_id} v{self.version}"


class DelegationLanguage(models.Model):
    """Język docelowy delegacji. Pozycja 1 to język domyślny uczniów tej delegacji."""

    delegation = models.ForeignKey(
        "accounts.Delegation", on_delete=models.CASCADE, related_name="translation_languages"
    )
    code = models.CharField("język", max_length=CODE_MAX_LENGTH)
    position = models.PositiveSmallIntegerField("kolejność", default=1)
    created_at = models.DateTimeField("zadeklarowany", default=timezone.now)

    objects = competition_scoped_manager("delegation__competition")

    class Meta:
        verbose_name = "język delegacji"
        verbose_name_plural = "języki delegacji"
        ordering = ("delegation", "position")
        constraints = [
            models.UniqueConstraint(fields=["delegation", "code"], name="problem_translations_language_once"),
            models.UniqueConstraint(
                fields=["delegation", "position"], name="problem_translations_language_position"
            ),
            # Jeden albo dwa języki (TR-01 § 1) – więz bazy, nie tylko formularza.
            models.CheckConstraint(
                condition=Q(position__gte=1) & Q(position__lte=2),
                name="problem_translations_language_position_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.delegation_id}: {self.code}"

    @property
    def label(self) -> str:
        return label(self.code)


class TranslationKind(models.TextChoices):
    TEXT = "TEXT", "tekst (Markdown)"
    PDF = "PDF", "plik PDF"


class TranslationStatus(models.TextChoices):
    DRAFT = "DRAFT", "szkic"
    SUBMITTED = "SUBMITTED", "czeka na akceptację"
    RETURNED = "RETURNED", "zwrócone do poprawy"
    APPROVED = "APPROVED", "zatwierdzone"


class Translation(models.Model):
    """Tłumaczenie zadania na jeden język – delegacji (tryb osobny) albo wspólne (``delegation`` pusta)."""

    problem = models.ForeignKey("competitions.Problem", on_delete=models.CASCADE, related_name="translations")
    language = models.CharField("język", max_length=CODE_MAX_LENGTH)
    #: ``NULL`` = tłumaczenie wspólne wszystkich delegacji tego języka (``SharingMode.SHARED``).
    delegation = models.ForeignKey(
        "accounts.Delegation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="problem_translations",
    )
    kind = models.CharField(
        "rodzaj", max_length=8, choices=TranslationKind.choices, default=TranslationKind.TEXT
    )
    #: Przetłumaczony tytuł – uczeń widzi go na swojej stronie zadania i w nagłówku pakietu do druku.
    title = models.CharField("tytuł", max_length=300, blank=True)
    body_md = models.TextField("treść (Markdown)", blank=True, max_length=BODY_MAX_LENGTH)
    #: Ten sam prywatny storage, co treść zadania: przetłumaczony arkusz jest tak samo tajny.
    pdf = models.FileField(
        "plik PDF", upload_to="problems/translations/", blank=True, storage=private_media_storage
    )
    pdf_sha256 = models.CharField("sha256 pliku", max_length=64, blank=True)
    status = models.CharField(
        "stan", max_length=16, choices=TranslationStatus.choices, default=TranslationStatus.DRAFT
    )
    #: Wersja oficjalna, z której tłumacz pracował przy ostatnim wysłaniu (albo założeniu szkicu).
    source_version = models.PositiveIntegerField("wersja źródła", default=1)
    #: Licznik zapisów – żeton optymistycznej współbieżności. Formularz edytora niesie wartość, z którą
    #: go wyrenderowano; zapis z inną wartością (drugi opiekun w trybie wspólnym, druga karta) dostaje
    #: odmowę zamiast cicho nadpisać cudzą pracę.
    edit_version = models.PositiveIntegerField("licznik zapisów", default=0)
    #: Kiedy wersja oficjalna zmieniła się pod tym tłumaczeniem. Czyści ją dopiero ponowne wysłanie.
    outdated_since = models.DateTimeField("nieaktualne od", null=True, blank=True)
    review_comment = models.TextField("komentarz komisji", blank=True, max_length=4000)
    approved_revision = models.ForeignKey(
        "TranslationRevision", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField("utworzone", default=timezone.now)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = competition_scoped_manager("problem__stage__edition__competition")

    class Meta:
        verbose_name = "tłumaczenie zadania"
        verbose_name_plural = "tłumaczenia zadań"
        ordering = ("problem", "language", "delegation_id")
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "language", "delegation"],
                condition=Q(delegation__isnull=False),
                name="problem_translations_one_per_delegation",
            ),
            models.UniqueConstraint(
                fields=["problem", "language"],
                condition=Q(delegation__isnull=True),
                name="problem_translations_one_shared",
            ),
        ]

    def __str__(self) -> str:
        return f"tłumaczenie {self.problem_id}/{self.language}/{self.delegation_id or 'wspólne'}"

    @property
    def language_label(self) -> str:
        return label(self.language)

    @property
    def language_name(self) -> str:
        return english_name(self.language)

    @property
    def is_outdated(self) -> bool:
        return self.outdated_since is not None

    @property
    def is_editable_status(self) -> bool:
        """Stany, w których opiekun pisze – zatwierdzone i wysłane są zablokowane (TR-01 § 2)."""
        return self.status in (TranslationStatus.DRAFT, TranslationStatus.RETURNED)

    @property
    def has_content(self) -> bool:
        if self.kind == TranslationKind.PDF:
            return bool(self.pdf)
        return bool((self.body_md or "").strip())


class RevisionDecision(models.TextChoices):
    APPROVED = "APPROVED", "zatwierdzona"
    RETURNED = "RETURNED", "zwrócona"
    #: Opiekun cofnął wysłanie, zanim komisja zdecydowała – wersja zostaje w historii.
    WITHDRAWN = "WITHDRAWN", "wycofana"


class TranslationRevision(models.Model):
    """Migawka tłumaczenia z chwili wysłania do akceptacji, z decyzją komisji."""

    translation = models.ForeignKey(Translation, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField("wersja")
    kind = models.CharField("rodzaj", max_length=8, choices=TranslationKind.choices)
    title = models.CharField("tytuł", max_length=300, blank=True)
    body_md = models.TextField("treść (Markdown)", blank=True)
    #: Nazwa pliku w storage – ten sam plik, co w tłumaczeniu w chwili wysłania. Nowe wgranie dostaje
    #: nową nazwę, więc migawka nie zmienia się pod nikim (pliku nie kasujemy, dopóki wskazuje go wersja).
    pdf = models.FileField("plik PDF", blank=True, storage=private_media_storage, max_length=300)
    pdf_sha256 = models.CharField("sha256 pliku", max_length=64, blank=True)
    source_version = models.PositiveIntegerField("wersja źródła")
    submitted_at = models.DateTimeField("wysłana", default=timezone.now)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decision = models.CharField("decyzja", max_length=16, choices=RevisionDecision.choices, blank=True)
    decided_at = models.DateTimeField("decyzja z", null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    comment = models.TextField("komentarz", blank=True)

    objects = competition_scoped_manager("translation__problem__stage__edition__competition")

    class Meta:
        verbose_name = "wersja tłumaczenia"
        verbose_name_plural = "wersje tłumaczeń"
        ordering = ("translation", "-number")
        constraints = [
            models.UniqueConstraint(
                fields=["translation", "number"], name="problem_translations_revision_number"
            ),
        ]

    def __str__(self) -> str:
        return f"wersja {self.number} tłumaczenia {self.translation_id}"


class StudentLanguage(models.Model):
    """Nadpisanie języka ucznia przez opiekuna drużyny (brak wiersza = język 1 delegacji)."""

    participant = models.OneToOneField(
        "accounts.Participant", on_delete=models.CASCADE, related_name="translation_language"
    )
    code = models.CharField("język", max_length=CODE_MAX_LENGTH)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    set_at = models.DateTimeField("ustawiony", default=timezone.now)

    objects = competition_scoped_manager("participant__competition")

    class Meta:
        verbose_name = "język ucznia"
        verbose_name_plural = "języki uczniów"

    def __str__(self) -> str:
        return f"{self.participant_id}: {self.code}"
