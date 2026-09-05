"""Modele rozwiązań: zgłoszenie (wersjonowane) i jego plik w prywatnym storage.

Zasady:
- plik nigdy nie jest identyfikowany nazwą od użytkownika – ``object_key`` buduje ``storage.py``
  z identyfikatorów technicznych i sumy sha256 (PROJEKT.md 1.3),
- filtr per rola jest w queryseckie (``Submission.objects.for_user``), nie w widoku (PROJEKT.md 2.2),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import GROUP_COORDINATOR
from apps.competitions.models import Problem, StageEntry


class SubmissionStatus(models.TextChoices):
    """Cykl życia zgłoszenia (PROJEKT.md 2.4). T-04 używa czterech pierwszych wartości."""

    SUBMITTED = "SUBMITTED", "oddane"
    SCANNING = "SCANNING", "skanowanie"
    REJECTED_INFECTED = "REJECTED_INFECTED", "odrzucone (wirus)"
    LOCKED = "LOCKED", "zablokowane"
    IN_REVIEW = "IN_REVIEW", "w ocenie"
    MODERATION = "MODERATION", "moderacja"
    GRADED_PROVISIONAL = "GRADED_PROVISIONAL", "ocena wstępna"
    APPEALED = "APPEALED", "reklamacja"
    FINAL = "FINAL", "ocena ostateczna"


class AvStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    # Stan trwały: skan nie ma jak się udać (brak obiektu w storage, plik ponad StreamMaxLength clamd).
    # Bez niego plik zostawałby na zawsze PENDING, a zgłoszenie w SCANNING – patrz apps.submissions.tasks.
    ERROR = "ERROR", "błąd skanu"


class SubmissionQuerySet(models.QuerySet):
    def for_user(self, user):
        """Widoczność per rola: uczestnik → własne, koordynator → wszystkie, recenzent → puste.

        Przydziały recenzentów dochodzą w T-05; do tego czasu recenzent nie widzi żadnego
        rozwiązania (domyślnie zamknięte, nie domyślnie otwarte).
        """
        if not user or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.groups.filter(name=GROUP_COORDINATOR).exists():
            return self
        participant = getattr(user, "participant", None)
        if participant is None:
            return self.none()
        return self.filter(entry__participant=participant)


class Submission(models.Model):
    """Jedno oddanie zadania przez uczestnika. Kolejne oddania to kolejne wersje, nie nadpisanie."""

    uuid = models.UUIDField("uuid", default=uuid.uuid4, unique=True, editable=False)
    entry = models.ForeignKey(StageEntry, on_delete=models.CASCADE, related_name="submissions")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="submissions")
    version = models.PositiveSmallIntegerField("wersja", default=1)
    submitted_at = models.DateTimeField("oddane", default=timezone.now)
    is_late = models.BooleanField("po deadline (w tolerancji)", default=False)
    status = models.CharField(
        "status", max_length=24, choices=SubmissionStatus.choices, default=SubmissionStatus.SUBMITTED
    )

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "rozwiązanie"
        verbose_name_plural = "rozwiązania"
        ordering = ("-submitted_at", "-version", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "problem", "version"], name="submissions_submission_unique_version"
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1), name="submissions_submission_version_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}/zad. {self.problem_id} v{self.version} ({self.status})"

    @property
    def latest_file(self) -> "SubmissionFile | None":
        """Najnowszy plik zgłoszenia. W praktyce jest dokładnie jeden, ale kolejność musi być jawna.

        ``order_by("-id")`` zamiast domyślnego porządku: jeśli kiedykolwiek pojawi się drugi plik,
        pobranie ma dać ten świeższy, a nie ten, który akurat wypadł pierwszy w ``Meta.ordering``.
        """
        return self.files.order_by("-id").first()

    @property
    def participant(self):
        return self.entry.participant


class SubmissionFile(models.Model):
    """Metadane pliku w prywatnym buckecie. Treść żyje wyłącznie w storage, nigdy w bazie."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="files")
    object_key = models.CharField("klucz obiektu", max_length=512, unique=True)
    sha256 = models.CharField("sha256", max_length=64)
    original_name = models.CharField("nazwa od użytkownika", max_length=255)
    mime = models.CharField("typ MIME", max_length=100)
    size_bytes = models.BigIntegerField("rozmiar (B)")
    av_status = models.CharField(
        "status skanu", max_length=16, choices=AvStatus.choices, default=AvStatus.PENDING
    )
    av_signature = models.CharField("sygnatura wirusa", max_length=200, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    scanned_at = models.DateTimeField("zeskanowany", null=True, blank=True)

    class Meta:
        verbose_name = "plik rozwiązania"
        verbose_name_plural = "pliki rozwiązań"
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.object_key} ({self.av_status})"

    @property
    def is_clean(self) -> bool:
        return self.av_status == AvStatus.CLEAN
