"""Modele domeny zawodów: edycja, etap, skala punktacji, próg kwalifikacji, zadanie, wpis do etapu.

Zasady:
- czas zawsze przez ``django.utils.timezone.now()`` (pola w UTC, ``USE_TZ=True``),
- reguły integralności są jednocześnie w ``clean()`` (czytelny ValidationError) i w bazie
  (constraint – ostatnia linia obrony przed zapisem z pominięciem ``full_clean()``),
- dostęp do danych wyłącznie przez ORM.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import GROUP_COORDINATOR, Participant

from .storage import private_media_storage

# Domyślna skala Olimpiady Matematycznej. Kolejność rosnąca jest częścią kontraktu (walidacja niżej).
DEFAULT_SCORING_VALUES: list[dict] = [
    {"value": 0, "label": "brak istotnego postępu"},
    {"value": 2, "label": "istotny postęp, rozwiązanie niepełne"},
    {"value": 5, "label": "rozwiązanie pełne z drobnymi usterkami"},
    {"value": 6, "label": "rozwiązanie pełne i poprawne"},
]
DEFAULT_MAX_VALUE = 6

# Formaty, jakie w ogóle wolno dopuścić dla zadania (twarda lista – walidacja uploadu w T-04).
SUPPORTED_FILE_FORMATS = ("pdf", "ipynb", "py")
DEFAULT_ALLOWED_FORMATS = ["pdf"]
DEFAULT_MAX_FILE_MB = 20
# Górna granica limitu na zadanie. Powyżej 100 MB plik i tak nie przeszedłby skanu: to
# ``StreamMaxLength`` clamd (``CLAMAV_STREAM_MAX_BYTES``), więc zgłoszenie utknęłoby bez werdyktu.
MAX_FILE_MB_LIMIT = 100


def default_scoring_values() -> list[dict]:
    """Kopia domyślnej skali – ``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return [dict(item) for item in DEFAULT_SCORING_VALUES]


def default_allowed_formats() -> list[str]:
    return list(DEFAULT_ALLOWED_FORMATS)


class Edition(models.Model):
    """Edycja olimpiady, np. „XV (2026/2027)”. Bieżąca może być tylko jedna."""

    year_label = models.CharField("oznaczenie edycji", max_length=64, unique=True)
    is_current = models.BooleanField("edycja bieżąca", default=False)
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    class Meta:
        verbose_name = "edycja"
        verbose_name_plural = "edycje"
        ordering = ("-created_at", "id")
        constraints = [
            # Częściowy indeks unikalny: dopuszcza wiele edycji archiwalnych, dokładnie jedną bieżącą.
            models.UniqueConstraint(
                fields=["is_current"],
                condition=Q(is_current=True),
                name="competitions_edition_single_current",
            ),
        ]

    def __str__(self) -> str:
        return self.year_label

    def clean(self) -> None:
        super().clean()
        if self.is_current:
            others = Edition.objects.filter(is_current=True).exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    {"is_current": "Bieżąca może być tylko jedna edycja. Odznacz poprzednią."}
                )


class StageKind(models.TextChoices):
    ELIM = "ELIM", "Eliminacje"
    DISTRICT = "DISTRICT", "Okręgowy"
    FINAL = "FINAL", "Finał"


class Stage(models.Model):
    """Etap edycji wraz z całą osią czasu: otwarcie, deadline (+grace), recenzje, okno reklamacji."""

    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="stages")
    kind = models.CharField("rodzaj", max_length=16, choices=StageKind.choices)
    opens_at = models.DateTimeField("otwarcie")
    deadline_at = models.DateTimeField("deadline oddania")
    grace_seconds = models.PositiveIntegerField("tolerancja po deadline (s)", default=0)
    review_deadline_at = models.DateTimeField("deadline recenzji")
    appeal_window_opens_at = models.DateTimeField("otwarcie okna reklamacji")
    appeal_window_closes_at = models.DateTimeField("zamknięcie okna reklamacji")
    results_published_at = models.DateTimeField("wyniki opublikowane", null=True, blank=True)
    # Ustawiany przez beat (``apps.submissions.tasks.close_due_stages``) po ``submission_deadline``.
    # Znacznik pełni też rolę bezpiecznika idempotencji: etap zamykamy dokładnie raz.
    closed_at = models.DateTimeField("etap zamknięty", null=True, blank=True)

    class Meta:
        verbose_name = "etap"
        verbose_name_plural = "etapy"
        ordering = ("edition", "opens_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["edition", "kind"], name="competitions_stage_unique_kind"),
            models.CheckConstraint(
                condition=Q(opens_at__lt=F("deadline_at")),
                name="competitions_stage_opens_before_deadline",
            ),
            models.CheckConstraint(
                condition=Q(deadline_at__lte=F("review_deadline_at")),
                name="competitions_stage_deadline_before_review",
            ),
            models.CheckConstraint(
                condition=Q(review_deadline_at__lte=F("appeal_window_opens_at")),
                name="competitions_stage_review_before_appeal",
            ),
            models.CheckConstraint(
                condition=Q(appeal_window_opens_at__lt=F("appeal_window_closes_at")),
                name="competitions_stage_appeal_window_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.edition.year_label} – {self.get_kind_display()}"

    def clean(self) -> None:
        super().clean()
        # Pominięte pola dają None; wtedy walidację kolejności robi walidacja pól, nie ta metoda.
        chain = (
            ("deadline_at", self.opens_at, self.deadline_at, False, "opens_at musi być przed deadline_at."),
            (
                "review_deadline_at",
                self.deadline_at,
                self.review_deadline_at,
                True,
                "review_deadline_at nie może być wcześniej niż deadline_at.",
            ),
            (
                "appeal_window_opens_at",
                self.review_deadline_at,
                self.appeal_window_opens_at,
                True,
                "appeal_window_opens_at nie może być wcześniej niż review_deadline_at.",
            ),
            (
                "appeal_window_closes_at",
                self.appeal_window_opens_at,
                self.appeal_window_closes_at,
                False,
                "appeal_window_closes_at musi być po appeal_window_opens_at.",
            ),
        )
        errors: dict[str, str] = {}
        for field, earlier, later, allow_equal, message in chain:
            if earlier is None or later is None:
                continue
            if later < earlier or (later == earlier and not allow_equal):
                errors[field] = message
        if errors:
            raise ValidationError(errors)

    @property
    def submission_deadline(self):
        """Twardy moment zamknięcia uploadu: ``deadline_at`` powiększony o tolerancję."""
        return self.deadline_at + timedelta(seconds=self.grace_seconds or 0)

    def is_open_for_submissions(self, now=None) -> bool:
        """Czy etap przyjmuje rozwiązania. Zawsze liczone po stronie serwera, w UTC."""
        now = now or timezone.now()
        return self.opens_at <= now < self.submission_deadline

    def is_appeal_window_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.appeal_window_opens_at <= now < self.appeal_window_closes_at

    def has_opened(self, now=None) -> bool:
        """Czy etap już się rozpoczął (treści zadań stają się jawne dopiero wtedy)."""
        now = now or timezone.now()
        return self.opens_at <= now


class ScoringScale(models.Model):
    """Parametryzacja skali punktowej etapu (domyślnie 0/2/5/6)."""

    stage = models.OneToOneField(Stage, on_delete=models.CASCADE, related_name="scoring_scale")
    values = models.JSONField("wartości skali", default=default_scoring_values)
    max_value = models.PositiveSmallIntegerField("maksimum", default=DEFAULT_MAX_VALUE)

    class Meta:
        verbose_name = "skala punktacji"
        verbose_name_plural = "skale punktacji"

    def __str__(self) -> str:
        return f"skala {sorted(self.allowed_values())} dla {self.stage_id}"

    def allowed_values(self) -> set[int]:
        """Zbiór dopuszczalnych ocen. Używany przy walidacji ``Review.score`` (T-05)."""
        result: set[int] = set()
        for item in self.values or []:
            value = item.get("value") if isinstance(item, dict) else None
            # bool jest podklasą int – True/False nie są ocenami (spójnie z clean()).
            if isinstance(value, int) and not isinstance(value, bool):
                result.add(value)
        return result

    def clean(self) -> None:
        super().clean()
        raw = self.values
        if not isinstance(raw, list) or not raw:
            raise ValidationError({"values": "Skala musi być niepustą listą pozycji {value, label}."})
        numbers: list[int] = []
        for item in raw:
            if not isinstance(item, dict) or "value" not in item or "label" not in item:
                raise ValidationError({"values": "Każda pozycja skali wymaga pól 'value' i 'label'."})
            value = item["value"]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError({"values": "Pole 'value' musi być nieujemną liczbą całkowitą."})
            if not isinstance(item["label"], str) or not item["label"].strip():
                raise ValidationError({"values": "Pole 'label' musi być niepustym tekstem."})
            numbers.append(value)
        if len(set(numbers)) != len(numbers):
            raise ValidationError({"values": "Wartości skali muszą być unikalne."})
        if numbers != sorted(numbers):
            raise ValidationError({"values": "Wartości skali muszą być uporządkowane rosnąco."})
        if 0 not in numbers:
            raise ValidationError({"values": "Skala musi zawierać wartość 0."})
        if self.max_value != max(numbers):
            raise ValidationError({"max_value": "max_value musi być równe największej wartości skali."})


class QualificationMode(models.TextChoices):
    MIN_POINTS = "MIN_POINTS", "min. punktów"
    TOP_N = "TOP_N", "najlepszych N"
    TOP_N_PER_DISTRICT = "TOP_N_PER_DISTRICT", "N na okręg"
    HYBRID = "HYBRID", "min. punktów ORAZ top N"


class QualificationRule(models.Model):
    """Próg kwalifikacji do następnego etapu. Przeliczenie robi T-07, tutaj tylko parametry."""

    stage = models.OneToOneField(Stage, on_delete=models.CASCADE, related_name="qualification_rule")
    mode = models.CharField(
        "tryb", max_length=32, choices=QualificationMode.choices, default=QualificationMode.MIN_POINTS
    )
    min_points = models.PositiveIntegerField("minimum punktów", null=True, blank=True)
    top_n = models.PositiveIntegerField("liczba kwalifikowanych", null=True, blank=True)

    class Meta:
        verbose_name = "próg kwalifikacji"
        verbose_name_plural = "progi kwalifikacji"

    def __str__(self) -> str:
        return f"{self.get_mode_display()} (min={self.min_points}, top={self.top_n})"

    @property
    def requires_min_points(self) -> bool:
        return self.mode in (QualificationMode.MIN_POINTS, QualificationMode.HYBRID)

    @property
    def requires_top_n(self) -> bool:
        return self.mode in (
            QualificationMode.TOP_N,
            QualificationMode.TOP_N_PER_DISTRICT,
            QualificationMode.HYBRID,
        )

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.requires_min_points and self.min_points is None:
            errors["min_points"] = f"Tryb {self.mode} wymaga podania min_points."
        if self.requires_top_n and self.top_n is None:
            errors["top_n"] = f"Tryb {self.mode} wymaga podania top_n."
        if self.requires_top_n and self.top_n is not None and self.top_n < 1:
            errors["top_n"] = "top_n musi być dodatnie."
        if errors:
            raise ValidationError(errors)


class Problem(models.Model):
    """Zadanie etapu. Treść PDF jest jawna dopiero po ``stage.opens_at`` (patrz serializery)."""

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="problems")
    number = models.PositiveSmallIntegerField("numer")
    title = models.CharField("tytuł", max_length=300)
    # Storage jawnie prywatny (patrz apps.competitions.storage): ``default`` jest w produkcji
    # publicznym bucketem Wagtaila, a treść zadania przed ``opens_at`` musi być nieosiągalna.
    statement_pdf = models.FileField(
        "treść (PDF)",
        upload_to="problems/statements/",
        blank=True,
        storage=private_media_storage,
    )
    allowed_formats = models.JSONField("dozwolone formaty", default=default_allowed_formats)
    max_file_mb = models.PositiveSmallIntegerField("limit rozmiaru pliku (MB)", default=DEFAULT_MAX_FILE_MB)

    class Meta:
        verbose_name = "zadanie"
        verbose_name_plural = "zadania"
        ordering = ("stage", "number")
        constraints = [
            models.UniqueConstraint(fields=["stage", "number"], name="competitions_problem_unique_number"),
            models.CheckConstraint(condition=Q(number__gte=1), name="competitions_problem_number_positive"),
            models.CheckConstraint(
                condition=Q(max_file_mb__gte=1), name="competitions_problem_max_file_mb_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_file_mb__lte=MAX_FILE_MB_LIMIT),
                name="competitions_problem_max_file_mb_under_limit",
            ),
        ]

    def __str__(self) -> str:
        return f"Zadanie {self.number}: {self.title}"

    def clean(self) -> None:
        super().clean()
        formats = self.allowed_formats
        if not isinstance(formats, list) or not formats:
            raise ValidationError({"allowed_formats": "Podaj co najmniej jeden dozwolony format."})
        unknown = [item for item in formats if item not in SUPPORTED_FILE_FORMATS]
        if unknown:
            raise ValidationError(
                {"allowed_formats": f"Niedozwolone formaty: {', '.join(map(str, unknown))}."}
            )
        if len(set(formats)) != len(formats):
            raise ValidationError({"allowed_formats": "Formaty nie mogą się powtarzać."})
        if self.max_file_mb is not None and not (1 <= self.max_file_mb <= MAX_FILE_MB_LIMIT):
            raise ValidationError(
                {"max_file_mb": f"Limit rozmiaru musi mieścić się w 1–{MAX_FILE_MB_LIMIT} MB."}
            )


class StageEntryStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "zarejestrowany"
    QUALIFIED = "QUALIFIED", "zakwalifikowany"
    NOT_QUALIFIED = "NOT_QUALIFIED", "niezakwalifikowany"
    DISQUALIFIED = "DISQUALIFIED", "zdyskwalifikowany"


class StageEntryQuerySet(models.QuerySet):
    def for_user(self, user):
        """Filtr per rola w jednym miejscu (PROJEKT.md 2.3): uczestnik widzi wyłącznie swoje wpisy."""
        if not user or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.groups.filter(name=GROUP_COORDINATOR).exists():
            return self
        participant = getattr(user, "participant", None)
        if participant is None:
            return self.none()
        return self.filter(participant=participant)


class StageEntry(models.Model):
    """Udział uczestnika w etapie. Dla ELIM powstaje przez rejestrację, dalej przez kwalifikację (T-07)."""

    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="stage_entries")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="entries")
    status = models.CharField(
        "status", max_length=16, choices=StageEntryStatus.choices, default=StageEntryStatus.REGISTERED
    )
    total_points = models.PositiveIntegerField("suma punktów", null=True, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    objects = StageEntryQuerySet.as_manager()

    class Meta:
        verbose_name = "wpis do etapu"
        verbose_name_plural = "wpisy do etapów"
        ordering = ("stage", "participant")
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "stage"], name="competitions_stageentry_unique_participant"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.participant.public_code} @ {self.stage_id} ({self.status})"
