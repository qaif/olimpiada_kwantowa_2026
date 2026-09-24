"""Ocena AI: sugestia punktów dla komitetu, liczona przez Claude'a (prośba organizatora z 24.09.2026).

Prośba brzmiała: koordynator dodaje klucz API, klika „Wygeneruj ocenę AI”, a model czyta pracę
uczestnika obok rozwiązania wzorcowego i proponuje ocenę, którą widzi recenzent. Trzy rzeczy
rozstrzygnięte w tym module i powód każdej z nich:

- **to jest sugestia, a nie ocena.** Żaden wiersz stąd nie trafia do ``grading.Review`` ani do
  ``grading.FinalGrade`` – oceną zostaje to, co wystawił człowiek. Dlatego model nie ma nawet
  klucza obcego do recenzji: nie ma ścieżki, którą propozycja mogłaby się „przelać” w wynik,
  a panel recenzenta może co najwyżej **wypełnić formularz** jako punkt wyjścia (art. 22 RODO:
  żadna decyzja nie zapada tu wyłącznie automatycznie),
- **jeden wiersz na (wersję pracy, dostawcę, model)**. Do v0.34.0 był jeden wiersz na wersję
  pracy; prośba o innych dostawców („żeby porównać”) dołożyła do klucza dostawcę i zamówiony
  model. „Wygeneruj ponownie” dalej nadpisuje wiersz **tej samej** trójki zamiast dokładać
  historię, a ocena innym modelem jest osobnym panelem obok. Koszt poprzednich przebiegów nie
  ginie – liczniki zużycia rosną w ``AiGradingSettings`` i przy samej ocenie,
- **klucze API są per konkurs i per dostawca, zaszyfrowane** (``apps.ai_grading.crypto``,
  :class:`AiProviderAccount`). Każdy organizator płaci za własne zużycie i odpowiada za własną
  umowę powierzenia z każdym dostawcą – klucz wspólny dla instalacji znaczyłby, że prace
  uczestników konkursu A idą na rachunek i na warunkach umowy organizatora B. Dostawca bez
  potwierdzonej umowy powierzenia nie dostaje ani jednej pracy uczestnika (wyjątek: **praca
  testowa** koordynatora, :class:`AiTestWork`, która danych uczestnika nie niesie).

Osobna aplikacja, a nie modele w ``apps.grading``: funkcja jest opcjonalna (flaga ``ai_grading``,
domyślnie wyłączona), ma własne zależności zewnętrzne (SDK dostawców) i własną ścieżkę danych
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
from apps.submissions.models import AvStatus, Submission

#: Przełącznik konkursu (``apps.tenancy.models.FEATURE_DEFAULTS``). Stała, a nie napis powtórzony
#: w każdym widoku: wyłączona funkcja ma znaczyć 404 pod **każdym** adresem i brak pozycji w menu.
AI_GRADING_FLAG = "ai_grading"


class AiProvider(models.TextChoices):
    """Dostawcy modelu. Wartość jest kluczem rejestru ``apps.ai_grading.providers``."""

    ANTHROPIC = "anthropic", "Anthropic (Claude)"
    OPENAI = "openai", "OpenAI (GPT)"
    GOOGLE = "google", "Google (Gemini)"
    META = "meta", "Meta (Muse Spark)"


class AiModel(models.TextChoices):
    """Modele Anthropic z v0.34.0. Identyfikatory dokładnie takie, jakie przyjmuje API.

    Zostaje jako stała zgodności; katalog wszystkich dostawców stoi w ``apps.ai_grading.catalog``.
    Domyślny jest Opus: ocenianie rozwiązań olimpijskich to zadanie, w którym dokładność jest
    ważniejsza od ceny, a o zejściu na tańszy model decyduje koordynator, nie kod.
    """

    OPUS = "claude-opus-5", "Claude Opus 5 (domyślny, dokładniejszy)"
    SONNET = "claude-sonnet-5", "Claude Sonnet 5 (tańszy)"


#: Najdłuższy identyfikator modelu, jaki przyjmujemy („inny identyfikator modelu”).
MODEL_ID_MAX_LENGTH = 100


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
    """Ustawienia oceny AI jednego konkursu: dostawca i model domyślne, ceny, limit i liczniki.

    Klucze dostawców leżą w :class:`AiProviderAccount` (do v0.34.0 był tu jeden klucz Anthropic –
    migracja ``0002`` przeniosła go bez odszyfrowywania).

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
    #: Dostawca i model **domyślne** – preselekcja w potwierdzeniu zlecenia. Model jest zwykłym
    #: napisem, nie wyborem z listy: identyfikatory modeli zmieniają się szybciej niż wydania
    #: serwisu, więc obok kuratorowanej listy jest „inny identyfikator modelu”.
    provider = models.CharField(
        "dostawca domyślny", max_length=16, choices=AiProvider.choices, default=AiProvider.ANTHROPIC
    )
    model = models.CharField("model domyślny", max_length=MODEL_ID_MAX_LENGTH, default=AiModel.OPUS)
    #: Ceny nadpisane przez koordynatora: ``{"dostawca/model": ["wejście", "wyjście"]}`` w USD za
    #: milion tokenów. Klucz bez wpisu bierze cenę domyślną z ``catalog.DEFAULT_PRICES``.
    price_overrides = models.JSONField("ceny modeli (nadpisane)", default=dict, blank=True)
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
    #: Wywołania modeli **bez ceny** – ich koszt nie wszedł do ``total_cost_usd``, więc limit
    #: wydatków ich nie widzi. Ekran pokazuje tę liczbę obok kwoty jako ostrzeżenie.
    total_unpriced_calls = models.PositiveIntegerField("wywołań bez znanej ceny", default=0)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ustawienia oceny AI"
        verbose_name_plural = "ustawienia oceny AI"

    def __str__(self) -> str:
        return f"ocena AI: {self.competition_id}"

    @property
    def limit_reached(self) -> bool:
        return self.spending_limit_usd is not None and self.total_cost_usd >= self.spending_limit_usd


class AiProviderAccount(models.Model):
    """Klucz API i potwierdzenie umowy powierzenia **jednego** dostawcy w jednym konkursie.

    Klucz jest **tylko do zapisu**: w bazie leży token Fernet (``api_key_encrypted``) i cztery
    ostatnie znaki do rozpoznania („kończy się na …abcd”). Pełnej wartości nie oddaje żaden ekran,
    eksport ani log – odszyfrowanie następuje wyłącznie w chwili budowania klienta SDK.

    Potwierdzenie umowy powierzenia (DPA) jest **oświadczeniem organizatora**, a nie ustawieniem
    technicznym: kto i kiedy potwierdził, stoi tutaj i w dzienniku zdarzeń. Bez niego dostawca nie
    dostaje ani jednej pracy uczestnika – każdy dostawca jest osobnym podmiotem przetwarzającym,
    w tym poza EOG, więc potwierdzenie jednego nie przenosi się na drugiego. Nie ma migracji, która
    by je „domniemała”: stan prawny wpisuje człowiek (panel albo komenda operatora
    ``confirm_ai_provider_dpa``).
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="ai_provider_accounts",
        verbose_name="konkurs",
    )
    provider = models.CharField("dostawca", max_length=16, choices=AiProvider.choices)
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
    dpa_confirmed_at = models.DateTimeField("umowa powierzenia potwierdzona", null=True, blank=True)
    dpa_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="umowę potwierdził",
    )
    dpa_note = models.CharField("uwaga do potwierdzenia", max_length=300, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "konto dostawcy AI"
        verbose_name_plural = "konta dostawców AI"
        ordering = ("competition_id", "provider")
        constraints = [
            models.UniqueConstraint(fields=["competition", "provider"], name="ai_provider_account_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}: {self.competition_id}"

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_encrypted)

    @property
    def masked_key(self) -> str:
        """Jedyna postać klucza, jaka trafia na ekran: „…abcd” albo pusty napis."""
        return f"…{self.api_key_last4}" if self.api_key_encrypted and self.api_key_last4 else ""

    @property
    def dpa_confirmed(self) -> bool:
        return self.dpa_confirmed_at is not None


class AiTestWork(models.Model):
    """Praca testowa koordynatora: własny przykładowy plik do wypróbowania dostawców.

    Prośba organizatora („włącz wszystkich dostawców dla testów”): przed potwierdzeniem umów
    powierzenia organizator chce zobaczyć, jak oceniają poszczególne modele. Praca testowa **nie**
    jest pracą uczestnika – nie ma wpisu na etap, kodu ani wersji – więc nie niesie danych osoby,
    której dotyczy umowa powierzenia, i dlatego bramka DPA jej nie dotyczy. Warunki, które to
    utrzymują w mocy (oświadczenie przy wgraniu, odmowa pliku identycznego z pracą uczestnika),
    stoją w ``services.upload_test_work``.

    Plik przechodzi tę samą walidację treści i ten sam skan antywirusowy, co praca uczestnika,
    i leży w tym samym prywatnym storage'u pod osobnym prefiksem ``ai-test/``.
    """

    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    problem = models.ForeignKey(
        "competitions.Problem",
        on_delete=models.CASCADE,
        related_name="ai_test_works",
        verbose_name="zadanie",
    )
    label = models.CharField("opis", max_length=120, blank=True)
    object_key = models.CharField("klucz obiektu", max_length=255, blank=True)
    sha256 = models.CharField("skrót SHA-256", max_length=64)
    mime = models.CharField("typ", max_length=60)
    size_bytes = models.PositiveBigIntegerField("rozmiar (B)")
    page_count = models.PositiveIntegerField("liczba stron", null=True, blank=True)
    av_status = models.CharField(
        "skan antywirusowy", max_length=16, choices=AvStatus.choices, default=AvStatus.PENDING
    )
    uploaded_at = models.DateTimeField("wgrana", default=timezone.now)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="wgrał",
    )

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "praca testowa AI"
        verbose_name_plural = "prace testowe AI"
        ordering = ("-uploaded_at", "-id")

    def __str__(self) -> str:
        return f"praca testowa {self.pk} (zadanie {self.problem_id})"

    @property
    def is_clean(self) -> bool:
        return self.av_status == AvStatus.CLEAN


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

    #: Konkurs wprost (kolumna denormalizacyjna), bo ocena pracy **testowej** nie ma pracy
    #: uczestnika, przez którą dało się dotąd dojść do konkursu.
    competition = models.ForeignKey(
        "tenancy.Competition", on_delete=models.CASCADE, related_name="+", verbose_name="konkurs"
    )
    #: Praca uczestnika **albo** praca testowa – dokładnie jedno z dwóch (ograniczenie niżej).
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_assessments",
        verbose_name="praca",
    )
    test_work = models.ForeignKey(
        AiTestWork,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assessments",
        verbose_name="praca testowa",
    )
    provider = models.CharField(
        "dostawca", max_length=16, choices=AiProvider.choices, default=AiProvider.ANTHROPIC
    )
    #: Model **zamówiony** – część klucza idempotencji (praca, dostawca, model).
    requested_model = models.CharField("model zamówiony", max_length=MODEL_ID_MAX_LENGTH, blank=True)
    status = models.CharField(
        "status", max_length=16, choices=AiAssessmentStatus.choices, default=AiAssessmentStatus.PENDING
    )
    #: Model, który **faktycznie** odpowiedział (``message.model``) – przy odmowie i przełączeniu
    #: ``fallbacks`` bywa inny niż zamówiony. Przed odpowiedzią: model zamówiony.
    model = models.CharField("model", max_length=MODEL_ID_MAX_LENGTH, blank=True)
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
    #: Kiedy materiały pracy **wyszły do dostawcy** – fakt przetwarzania przez podmiot
    #: przetwarzający, który trafia do eksportu danych uczestnika (art. 15 ust. 1 lit. c RODO).
    sent_at = models.DateTimeField("przekazana do dostawcy", null=True, blank=True)
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
    #: ``False``, gdy choć jeden przebieg dotyczył modelu bez ceny – ``cost_usd`` jest wtedy
    #: dolnym oszacowaniem, a ekran pisze „koszt nieznany”.
    cost_known = models.BooleanField("koszt znany", default=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ocena AI"
        verbose_name_plural = "oceny AI"
        ordering = ("submission_id", "-requested_at", "-id")
        indexes = [models.Index(fields=["status", "dispatched_at"], name="ai_assessment_queue_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(submission__isnull=False, test_work__isnull=True)
                    | models.Q(submission__isnull=True, test_work__isnull=False)
                ),
                name="ai_assessment_one_source",
            ),
            models.UniqueConstraint(
                fields=["submission", "provider", "requested_model"],
                condition=models.Q(submission__isnull=False),
                name="ai_assessment_unique_submission_model",
            ),
            models.UniqueConstraint(
                fields=["test_work", "provider", "requested_model"],
                condition=models.Q(test_work__isnull=False),
                name="ai_assessment_unique_test_model",
            ),
        ]

    def __str__(self) -> str:
        source = (
            f"pracy {self.submission_id}" if self.submission_id else f"pracy testowej {self.test_work_id}"
        )
        return f"ocena AI {source} – {self.provider} {self.requested_model} ({self.status})"

    @property
    def is_test(self) -> bool:
        return self.test_work_id is not None

    @property
    def provider_label(self) -> str:
        return AiProvider(self.provider).label if self.provider in AiProvider.values else self.provider

    @property
    def is_done(self) -> bool:
        return self.status == AiAssessmentStatus.DONE

    @property
    def is_in_progress(self) -> bool:
        return self.status in (AiAssessmentStatus.PENDING, AiAssessmentStatus.RUNNING)
