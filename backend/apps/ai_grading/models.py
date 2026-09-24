"""Ocena AI: sugestia punktów dla komitetu, liczona przez Claude'a (prośba organizatora z 24.09.2026).

Prośba brzmiała: koordynator dodaje klucz API, klika „Wygeneruj ocenę AI”, a model czyta pracę
uczestnika obok rozwiązania wzorcowego i proponuje ocenę, którą widzi recenzent. Trzy rzeczy
rozstrzygnięte w tym module i powód każdej z nich:

- **to jest sugestia, a nie ocena.** Żaden wiersz stąd nie trafia do ``grading.Review`` ani do
  ``grading.FinalGrade`` – oceną zostaje to, co wystawił człowiek. Dlatego model nie ma nawet
  klucza obcego do recenzji: nie ma ścieżki, którą propozycja mogłaby się „przelać” w wynik,
  a panel recenzenta może co najwyżej **wypełnić formularz** jako punkt wyjścia (art. 22 RODO:
  żadna decyzja nie zapada tu wyłącznie automatycznie),
- **jeden wiersz na wersję pracy** (``OneToOne`` z ``Submission``, a każda wersja jest osobną
  pracą). „Wygeneruj ponownie” nadpisuje ten sam wiersz zamiast dokładać historię: recenzent ma
  widzieć jedną, aktualną sugestię, a nie wybierać spośród kilku. Koszt poprzednich przebiegów
  nie ginie – liczniki zużycia rosną w ``AiGradingSettings`` i przy samej ocenie,
- **klucz API jest per konkurs i leży zaszyfrowany** (``apps.ai_grading.crypto``). Każdy
  organizator płaci za własne zużycie i odpowiada za własną umowę powierzenia z Anthropic – klucz
  wspólny dla instalacji znaczyłby, że prace uczestników konkursu A idą na rachunek i na
  warunkach umowy organizatora B.

Osobna aplikacja, a nie modele w ``apps.grading``: funkcja jest opcjonalna (flaga ``ai_grading``,
domyślnie wyłączona), ma własną zależność zewnętrzną (SDK ``anthropic``) i własną ścieżkę danych
poza serwer. Trzymanie jej obok recenzji wiązałoby rdzeń oceniania z dostawcą, którego konkurs
może w ogóle nie używać.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.models import Stage
from apps.competitions.scoping import competition_scoped_manager
from apps.submissions.models import Submission

#: Przełącznik konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``). Stała, a nie napis powtórzony
#: w każdym widoku: wyłączona funkcja ma znaczyć 404 pod **każdym** adresem i brak pozycji w menu.
AI_GRADING_FLAG = "ai_grading"


class AiModel(models.TextChoices):
    """Modele do wyboru. Identyfikatory dokładnie takie, jakie przyjmuje API – bez sufiksu daty.

    Domyślny jest Opus: ocenianie rozwiązań olimpijskich to zadanie, w którym dokładność jest
    ważniejsza od ceny, a o zejściu na tańszy model decyduje koordynator, nie kod.
    """

    OPUS = "claude-opus-5", "Claude Opus 5 (domyślny, dokładniejszy)"
    SONNET = "claude-sonnet-5", "Claude Sonnet 5 (tańszy)"


#: Cennik w USD za milion tokenów: (wejście, wyjście). Stan z 24.09.2026. Służy **wyłącznie**
#: szacunkom na ekranie koordynatora – rozliczenie wystawia Anthropic i to jego faktura jest
#: prawdą. Model spoza tabeli (np. ten, na który przełączył żądanie mechanizm ``fallbacks``)
#: liczymy stawkami modelu wybranego przez koordynatora: szacunek ma być zachowawczy, a nie pusty.
PRICING_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    AiModel.OPUS.value: (Decimal("5"), Decimal("25")),
    AiModel.SONNET.value: (Decimal("2"), Decimal("10")),
    # Cel przełączenia ``fallbacks: "default"`` przy odmowie kategorii „cyber” – te same stawki co
    # Opus 5, więc rachunek po przełączeniu nie kłamie.
    "claude-opus-4-8": (Decimal("5"), Decimal("25")),
}
#: Zapis do cache promptu kosztuje 1,25 stawki wejścia, odczyt – 0,1.
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
CACHE_READ_MULTIPLIER = Decimal("0.1")


class AiAssessmentStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje"
    RUNNING = "RUNNING", "w toku"
    DONE = "DONE", "gotowa"
    ERROR = "ERROR", "błąd"


class AiConfidence(models.TextChoices):
    """Pewność deklarowana przez model. Wartości są **tymi samymi napisami**, co w schemacie JSON."""

    LOW = "niska", "niska"
    MEDIUM = "średnia", "średnia"
    HIGH = "wysoka", "wysoka"


class AiGradingSettings(models.Model):
    """Ustawienia oceny AI jednego konkursu: klucz, model, limit wydatków i liczniki zużycia.

    Klucz jest **tylko do zapisu**: w bazie leży token Fernet (``api_key_encrypted``) i cztery
    ostatnie znaki do rozpoznania („kończy się na …abcd”). Pełnej wartości nie oddaje żaden ekran,
    eksport ani log – odszyfrowanie następuje wyłącznie w chwili budowania klienta SDK
    (``apps.ai_grading.client``).

    Liczniki zużycia są tutaj, a nie wyłącznie przy ocenach, bo ocena potrafi zniknąć (kaskada po
    skasowanej pracy, anonimizacja konta), a pytanie „ile nas to już kosztowało” ma mieć odpowiedź
    niezależną od tego, co zostało w tabeli ocen.
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="ai_grading_settings",
        verbose_name="konkurs",
    )
    api_key_encrypted = models.TextField("klucz API (zaszyfrowany)", blank=True)
    api_key_last4 = models.CharField("ostatnie znaki klucza", max_length=4, blank=True)
    api_key_set_at = models.DateTimeField("klucz ustawiony", null=True, blank=True)
    api_key_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="klucz ustawił",
    )
    #: Wynik ostatniego „Sprawdź klucz”. ``None`` znaczy „nie sprawdzano od ostatniej zmiany”.
    api_key_checked_at = models.DateTimeField("klucz sprawdzony", null=True, blank=True)
    api_key_check_ok = models.BooleanField("klucz działa", null=True, blank=True)
    api_key_check_message = models.CharField("wynik sprawdzenia", max_length=300, blank=True)
    model = models.CharField("model", max_length=40, choices=AiModel.choices, default=AiModel.OPUS)
    #: Twardy limit wydatków w USD (szacunek z liczników niżej). Puste = bez limitu. Po jego
    #: przekroczeniu nowe zlecenia są odrzucane, a oceny czekające w kolejce kończą się błędem
    #: zamiast wołać API – to jest bezpiecznik na „kliknąłem 400 prac i poszło”.
    spending_limit_usd = models.DecimalField(
        "limit wydatków (USD)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    total_calls = models.PositiveIntegerField("wywołań API", default=0)
    total_input_tokens = models.PositiveBigIntegerField("tokeny wejścia", default=0)
    total_output_tokens = models.PositiveBigIntegerField("tokeny wyjścia", default=0)
    total_cache_write_tokens = models.PositiveBigIntegerField("tokeny zapisu cache", default=0)
    total_cache_read_tokens = models.PositiveBigIntegerField("tokeny odczytu cache", default=0)
    total_cost_usd = models.DecimalField(
        "szacowany koszt (USD)", max_digits=12, decimal_places=6, default=Decimal("0")
    )
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ustawienia oceny AI"
        verbose_name_plural = "ustawienia oceny AI"

    def __str__(self) -> str:
        return f"ocena AI: {self.competition_id}"

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_encrypted)

    @property
    def masked_key(self) -> str:
        """Jedyna postać klucza, jaka trafia na ekran: „…abcd” albo pusty napis."""
        return f"…{self.api_key_last4}" if self.api_key_encrypted and self.api_key_last4 else ""

    @property
    def limit_reached(self) -> bool:
        return self.spending_limit_usd is not None and self.total_cost_usd >= self.spending_limit_usd


class AiStageVisibility(models.Model):
    """Czy uczestnicy etapu widzą ocenę AI swojej pracy – decyzja koordynatora, domyślnie **nie**.

    Osobny wiersz, a nie kolumna ``Stage``: etap jest korzeniem danych zawodów i czyta go każdy
    ekran serwisu, a to ustawienie ma znaczenie wyłącznie dla konkursu z włączoną oceną AI. Brak
    wiersza znaczy „nie pokazuj” – czyli dokładnie to, co wiersz z ``False``.

    Nawet przy ``True`` uczestnik widzi sugestię dopiero **po ogłoszeniu wyników** etapu i obok
    oficjalnej oceny, nigdy zamiast niej (``apps.ai_grading.services.participant_ai_feedback``).
    """

    stage = models.OneToOneField(
        Stage, on_delete=models.CASCADE, related_name="ai_visibility", verbose_name="etap"
    )
    show_to_participants = models.BooleanField("pokaż uczestnikom ocenę AI", default=False)
    changed_at = models.DateTimeField("zmienione", default=timezone.now)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zmienił",
    )

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "widoczność oceny AI"
        verbose_name_plural = "widoczność oceny AI"

    def __str__(self) -> str:
        return f"etap {self.stage_id}: {'widoczna' if self.show_to_participants else 'ukryta'}"


class AiAssessment(models.Model):
    """Sugestia oceny jednej wersji pracy. Pola wyniku są puste, dopóki status nie jest ``DONE``.

    Wiersz nie trzyma **niczego**, co identyfikuje uczestnika: droga do osoby prowadzi wyłącznie
    przez ``submission``, tak samo jak przy recenzji. Panel recenzenta pokazuje go przy pracy,
    którą recenzent i tak ma przydzieloną, więc anonimowość oceniania zostaje nietknięta.
    """

    submission = models.OneToOneField(
        Submission, on_delete=models.CASCADE, related_name="ai_assessment", verbose_name="praca"
    )
    status = models.CharField(
        "status", max_length=16, choices=AiAssessmentStatus.choices, default=AiAssessmentStatus.PENDING
    )
    #: Model, który **faktycznie** odpowiedział (``message.model``) – przy odmowie i przełączeniu
    #: ``fallbacks`` bywa inny niż zamówiony. Przed odpowiedzią: model zamówiony.
    model = models.CharField("model", max_length=60, blank=True)
    requested_at = models.DateTimeField("zlecona", default=timezone.now)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zlecił",
    )
    #: Kiedy zadanie trafiło do kolejki Celery. ``None`` przy ``PENDING`` znaczy „czeka na wolne
    #: miejsce” – ogranicznik współbieżności (``services.pump``) wypuszcza prace po kolei.
    dispatched_at = models.DateTimeField("przekazana do kolejki", null=True, blank=True)
    started_at = models.DateTimeField("rozpoczęta", null=True, blank=True)
    #: Kiedy materiały pracy **wyszły do Anthropic** – fakt przetwarzania przez podmiot
    #: przetwarzający, który trafia do eksportu danych uczestnika (art. 15 ust. 1 lit. c RODO).
    sent_at = models.DateTimeField("przekazana do Anthropic", null=True, blank=True)
    finished_at = models.DateTimeField("zakończona", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("próby", default=0)

    proposed_points = models.DecimalField(
        "proponowane punkty", max_digits=6, decimal_places=2, null=True, blank=True
    )
    max_points = models.DecimalField("maksimum", max_digits=6, decimal_places=2, null=True, blank=True)
    criteria = models.JSONField("kryteria", default=list, blank=True)
    summary = models.TextField("podsumowanie", blank=True)
    errors = models.JSONField("błędy w pracy", default=list, blank=True)
    confidence = models.CharField("pewność", max_length=16, choices=AiConfidence.choices, blank=True)
    injection_suspected = models.BooleanField("podejrzenie próby manipulacji", default=False)

    #: Komunikat dla koordynatora – **nasz** tekst, nigdy surowa treść wyjątku SDK (ta bywa
    #: długa, angielska i niesie nagłówki odpowiedzi).
    error_code = models.CharField("kod błędu", max_length=32, blank=True)
    error_message = models.CharField("błąd", max_length=500, blank=True)
    request_id = models.CharField("identyfikator żądania", max_length=80, blank=True)

    input_tokens = models.PositiveIntegerField("tokeny wejścia", default=0)
    output_tokens = models.PositiveIntegerField("tokeny wyjścia", default=0)
    cache_write_tokens = models.PositiveIntegerField("tokeny zapisu cache", default=0)
    cache_read_tokens = models.PositiveIntegerField("tokeny odczytu cache", default=0)
    #: Koszt **wszystkich** przebiegów tej pracy (szacunek). Rośnie przy „wygeneruj ponownie”.
    cost_usd = models.DecimalField("szacowany koszt (USD)", max_digits=10, decimal_places=6, default=0)

    #: Przez pracę, czyli przez jej kolumnę denormalizacyjną – jedno złączenie, nie cztery.
    objects = competition_scoped_manager("submission__competition")

    class Meta:
        verbose_name = "ocena AI"
        verbose_name_plural = "oceny AI"
        ordering = ("submission_id",)
        indexes = [models.Index(fields=["status", "dispatched_at"], name="ai_assessment_queue_idx")]

    def __str__(self) -> str:
        return f"ocena AI pracy {self.submission_id} ({self.status})"

    @property
    def is_done(self) -> bool:
        return self.status == AiAssessmentStatus.DONE

    @property
    def is_in_progress(self) -> bool:
        return self.status in (AiAssessmentStatus.PENDING, AiAssessmentStatus.RUNNING)
